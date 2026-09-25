"""Vigía de sincronización (cron). Deja registro de los sensores y Qbox que no
sincronizan con la plataforma, y de por qué.

Es un observador: NO toca el flujo. No modifica sensors ni tboxes, no da de alta nada
en la plataforma (allá solo consulta) y no cambia el worker, el resync ni el cron de
reconciliación. Su única escritura es asset_audit_log, que es best-effort.

En cada corrida:
  1. Lee los activos atorados (sin daijin_id, no borrados).
  2. Diagnostica cada uno solo leyendo: código inválido, ya está en la plataforma (el
     cron lo resolverá), no está en la plataforma, o la plataforma lo rechazó (se toma
     el último error que haya quedado en la bitácora, ej. "537: 传感器已存在").
  3. Escribe una fila SOLO cuando algo cambia: se atoró, cambió su diagnóstico, o se
     resolvió (sincronizó o se borró). Compara contra su propia última fila por activo,
     así que no necesita tabla propia.

Las filas llevan actor='sync-watcher' y action='reconcile' (valor que ya existe en el
ENUM de la columna, para no migrar). result: pending = atorado pero puede salir;
failed = no sale solo (código inválido o rechazo de la plataforma); success = resuelto.
"""
import json
import logging

from shared.activation import NotOnPlatform, PlatformUnavailable, confirm_on_platform
from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_in, get_where
from shared.smarttyre.client import SmartTyreClient
from shared.utils.validators import HEX12

_log = logging.getLogger(__name__)

ACTOR = "sync-watcher"
AUDIT_TABLE = "asset_audit_log"

# Tope de activos atorados que se revisan por corrida y por tabla. Hoy son un puñado;
# el tope evita que un rezago grande se coma el timeout.
BATCH = 200
# Filas de bitácora que se leen para reconstruir el último estado de cada activo.
HISTORY = 5000

RESOURCES = [
    {"table": "sensors", "asset_type": "sensor", "key": "sensorCode"},
    {"table": "tboxes", "asset_type": "tbox", "key": "tboxCode"},
]

# diagnóstico -> (result, texto para la columna error)
DIAGNOSTICOS = {
    "codigo_invalido": (
        "failed", "El código no son 12 caracteres hexadecimales: la plataforma nunca lo va a aceptar."),
    "ya_existe_en_otra_cuenta": (
        "failed", "La plataforma dice que ya existe, pero no aparece en nuestra organización: "
                  "probablemente está dado de alta en otra cuenta. Hay que pedirlo al proveedor."),
    "rechazado_por_la_plataforma": (
        "failed", "La plataforma rechazó el alta. Ver ultimo_error_plataforma."),
    "no_esta_en_la_plataforma": (
        "pending", "No está dado de alta en la plataforma. Lo crea la carga masiva o el reintento de sincronización."),
    "en_la_plataforma_sin_id_local": (
        "pending", "Ya está en la plataforma pero sin id local: el cron de reconciliación lo resuelve solo."),
}


def _json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def _ultimo_estado(db, asset_type):
    """{asset_id: fila más reciente del vigía} para este tipo de activo."""
    rows = get_where(db, AUDIT_TABLE, "asset_type = %s AND actor = %s",
                     [asset_type, ACTOR], limit=HISTORY, order="DESC")
    ultimo = {}
    for r in rows:
        ultimo.setdefault(r.get("asset_id"), r)
    return ultimo


def _errores_de_plataforma(db, asset_type, ids):
    """{asset_id: último error que dejó otro proceso en la bitácora} para esos ids."""
    if not ids:
        return {}
    marcas = ", ".join(["%s"] * len(ids))
    rows = get_where(
        db, AUDIT_TABLE,
        f"asset_type = %s AND actor <> %s AND error IS NOT NULL AND asset_id IN ({marcas})",
        [asset_type, ACTOR, *ids], limit=HISTORY, order="DESC")
    errores = {}
    for r in rows:
        errores.setdefault(r.get("asset_id"), r.get("error"))
    return errores


def _rendiciones_del_worker(db, asset_type):
    """{asset_id: veces que el worker de sincronización se rindió con ese activo}."""
    rows = get_where(db, AUDIT_TABLE, "asset_type = %s AND natural_key LIKE %s",
                     [asset_type, "bulk:giveup:%"], limit=HISTORY, order="DESC")
    cuenta = {}
    for r in rows:
        for rid in _json(r.get("payload")).get("pending_ids") or []:
            cuenta[rid] = cuenta.get(rid, 0) + 1
    return cuenta


def diagnosticar(row, key, table, st, ultimo_error):
    """Diagnóstico de un activo atorado, o None si no se pudo preguntar a la plataforma
    (una lectura fallida no es prueba de nada: esa corrida no escribe)."""
    code = str(row.get(key) or "")
    if not HEX12.match(code):
        return "codigo_invalido", {}
    if st is None:
        return None, {}
    try:
        daijin_id = confirm_on_platform(row, table, client=st, backoff=(0.0,))
        return "en_la_plataforma_sin_id_local", {"daijin_en_plataforma": daijin_id}
    except NotOnPlatform:
        pass
    except PlatformUnavailable:
        return None, {}
    if ultimo_error and "537" in str(ultimo_error):
        return "ya_existe_en_otra_cuenta", {}
    if ultimo_error:
        return "rechazado_por_la_plataforma", {}
    return "no_esta_en_la_plataforma", {}


def _anotar(db, context, *, asset_type, row, key, result, error, payload):
    audit(db, None, context, actor=ACTOR, action="reconcile", asset_type=asset_type,
          asset_id=row.get("id"), natural_key=row.get(key), company_id=row.get("company_id"),
          daijin_id=row.get("daijin_id"), result=result, error=error, payload=payload)


def revisar_recurso(db, st, cfg, context, resumen):
    table, asset_type, key = cfg["table"], cfg["asset_type"], cfg["key"]
    atorados = get_where(db, t(table), "daijin_id IS NULL AND (is_deleted IS NULL OR is_deleted = 0)",
                         [], limit=BATCH, order="ASC")
    ultimo = _ultimo_estado(db, asset_type)
    errores = _errores_de_plataforma(db, asset_type, [r["id"] for r in atorados])
    rendiciones = _rendiciones_del_worker(db, asset_type)

    # 1. Atorados: anotar si es nuevo o si cambió el diagnóstico.
    atorados_ids = set()
    for row in atorados:
        atorados_ids.add(row["id"])
        try:
            diag, extra = diagnosticar(row, key, table, st, errores.get(row["id"]))
            if diag is None:
                resumen["sin_plataforma"] += 1
                continue
            result, texto = DIAGNOSTICOS[diag]
            previo = ultimo.get(row["id"])
            if previo and previo.get("result") == result and \
                    _json(previo.get("payload")).get("diagnostico") == diag:
                resumen["sin_cambio"] += 1
                continue
            _anotar(db, context, asset_type=asset_type, row=row, key=key, result=result, error=texto,
                    payload={"diagnostico": diag, "lote": row.get("batch_code"),
                             "status": row.get("status"),
                             "rendiciones_del_worker": rendiciones.get(row["id"], 0),
                             "ultimo_error_plataforma": errores.get(row["id"]), **extra})
            resumen["anotados"] += 1
        except Exception as e:  # una fila no tumba al resto
            _log.warning("sync-watcher %s id=%s: %s", asset_type, row.get("id"), e)
            resumen["errores"] += 1

    # 2. Los que el vigía tenía como atorados y ya no lo están: se resolvieron o se borraron.
    pendientes = [aid for aid, r in ultimo.items()
                  if r.get("result") in ("pending", "failed") and aid not in atorados_ids]
    for row in get_in(db, t(table), "id", pendientes) if pendientes else []:
        try:
            if row.get("daijin_id") and not row.get("is_deleted"):
                diag, texto = "resuelto", f"Sincronizado con la plataforma (id {row.get('daijin_id')})."
            elif row.get("is_deleted"):
                diag, texto = "borrado", "Se borró sin haber sincronizado."
            else:
                continue  # sigue atorado pero quedó fuera del cupo de esta corrida
            _anotar(db, context, asset_type=asset_type, row=row, key=key, result="success",
                    error=None, payload={"diagnostico": diag, "detalle": texto,
                                         "lote": row.get("batch_code")})
            resumen["resueltos"] += 1
        except Exception as e:
            _log.warning("sync-watcher %s id=%s: %s", asset_type, row.get("id"), e)
            resumen["errores"] += 1


def handler(event, context):
    db = get_db()
    try:
        st = SmartTyreClient()
    except Exception as e:
        # Sin la plataforma solo se puede diagnosticar el código inválido; el resto espera.
        _log.warning("sync-watcher: plataforma no disponible: %s", e)
        st = None

    resumen = {"anotados": 0, "resueltos": 0, "sin_cambio": 0, "sin_plataforma": 0, "errores": 0}
    for cfg in RESOURCES:
        try:
            revisar_recurso(db, st, cfg, context, resumen)
        except Exception as e:
            _log.warning("sync-watcher %s: %s", cfg["asset_type"], e)
            resumen["errores"] += 1
    return {"status": "ok", **resumen}

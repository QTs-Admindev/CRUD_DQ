"""Lambda de reconciliación (cron). Cierra lo que quedó a medias con Dajin.

Dos barridos idempotentes sobre los 4 activos:

  A. CREATES pendientes  (status = 'registering')
     El create escribió local pero no confirmó el daijin_id (Dajin no respondió a
     tiempo). Se re-resuelve el id por la llave natural vía la OpenAPI y se activa.

  B. BORRADOS pendientes  (is_deleted = 1 AND daijin_id IS NOT NULL)
     El delete marcó local pero no pudo borrar en Dajin (fallo transitorio). Se
     reintenta el borrado remoto vía basic-api; al confirmar se limpia el daijin_id.

Es best-effort y acotado (LIMIT por barrido): si algo falla, se retoma en la
siguiente corrida. Cada fila va en su propio try para que una no tumbe al resto.
"""
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_where, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete  # noqa: F401 (TRANSIENT: parte del contrato del módulo)
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import _find_id
from shared.utils.clock import now_ms

# Cuántas filas procesar por barrido y por tabla (para no pasarnos del timeout).
BATCH = 100

# table: tabla local · resource: recurso en basic-api · list_path: GET de la OpenAPI ·
# key: llave natural para el GET · active: status al re-resolver un create.
ASSETS = [
    {"table": "units", "resource": "vehicle",
     "list_path": "/smartyre/openapi/vehicle/list",
     "key": lambda r: {"licensePlateNumber": str(r["id"])}, "active": "active"},
    {"table": "tires", "resource": "tyre",
     "list_path": "/smartyre/openapi/tyre/list",
     "key": lambda r: {"tyreCode": str(r["id"])}, "active": "new"},
    {"table": "sensors", "resource": "sensor",
     "list_path": "/smartyre/openapi/sensor/list",
     "key": lambda r: {"sensorCode": r["sensorCode"]}, "active": "active"},
    {"table": "tboxes", "resource": "tbox",
     "list_path": "/smartyre/openapi/tbox/list",
     "key": lambda r: {"tboxCode": r["tboxCode"]}, "active": "active"},
]


def handler(event, context):
    db = get_db()
    try:
        st = SmartTyreClient()
    except Exception as e:
        # Sin OpenAPI no podemos resolver ni verificar existencia; abortamos con detalle.
        return {"error": f"SmartTyre auth falló: {e}"}

    summary = {"resolved": 0, "deleted": 0, "guard_blocked": 0, "errors": 0}

    for cfg in ASSETS:
        table = t(cfg["table"])
        _sweep_registering(db, st, table, cfg, summary)
        _sweep_pending_deletes(db, st, table, cfg, summary)

    return {"status": "ok", **summary}


def _sweep_registering(db, st, table, cfg, summary):
    """A. Re-resuelve el daijin_id de los creates atorados en 'registering'."""
    rows = get_where(db, table, "status = %s AND is_deleted = 0", ["registering"], BATCH)
    for r in rows:
        try:
            found = _find_id(st, cfg["list_path"], cfg["key"](r))
            if found is None:
                continue  # aún no aparece en Dajin; se reintenta la próxima corrida
            update(db, table, r["id"], {
                "daijin_id": found, "status": cfg["active"], "updated_at": now_ms(),
            })
            db.commit()
            summary["resolved"] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _sweep_pending_deletes(db, st, table, cfg, summary):
    """B. Reintenta el borrado en Dajin de los que quedaron is_deleted=1 con daijin_id."""
    rows = get_where(db, table, "is_deleted = 1 AND daijin_id IS NOT NULL", [], BATCH)
    for r in rows:
        try:
            status, msg = attempt_delete(cfg["resource"], str(r["daijin_id"]))
            if status == DONE:
                _clear_daijin(db, table, r["id"])
                summary["deleted"] += 1
            elif status == GUARD:
                # ¿"ya no existe" (idempotente) o un guard real (ej. 531 con sensor)?
                if _find_id(st, cfg["list_path"], cfg["key"](r)) is None:
                    _clear_daijin(db, table, r["id"])  # ya estaba borrado en Dajin
                    summary["deleted"] += 1
                else:
                    summary["guard_blocked"] += 1  # necesita acción manual (desvincular)
            # TRANSIENT -> se deja para la próxima corrida
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _clear_daijin(db, table, rid):
    update(db, table, rid, {"daijin_id": None, "updated_at": now_ms()})
    db.commit()

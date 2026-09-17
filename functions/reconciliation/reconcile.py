"""Lambda de reconciliación (cron). Cierra lo que quedó a medias con la plataforma.

Barridos idempotentes sobre los activos:

  A. CREATES pendientes  (daijin_id IS NULL)
     El create escribió local pero no confirmó el daijin_id (la plataforma no respondió a
     tiempo). Se re-resuelve el id por la llave natural vía la OpenAPI y se activa. La
     selección es por la ausencia del id y no por el status, que es editable.

  B. BORRADOS pendientes  (is_deleted = 1 AND daijin_id IS NOT NULL)
     El delete marcó local pero no pudo borrar en la plataforma (fallo transitorio). Se
     reintenta el borrado remoto vía basic-api; al confirmar se limpia el daijin_id.

  C. LIGAS divergentes  (Qbox<->vehículo, llanta<->vehículo, sensor<->llanta)
     Nuestra BD dice que están ligados pero la plataforma NO lo refleja (bind fallido, un
     no-op, o el activo se borró del lado de la plataforma). Se re-lee la plataforma; si la
     liga no está, se re-crea el activo si es fantasma y se re-manda el bind, verificando el
     resultado. Esto sana divergencias existentes sin que nadie tenga que re-crear nada.

Es best-effort y acotado (LIMIT por barrido): si algo falla, se retoma en la
siguiente corrida. Cada fila va en su propio try para que una no tumbe al resto.
"""
from shared.activation import (
    NotOnPlatform, PlatformUnavailable, confirm_on_platform, liberar_llave_natural,
)
import os
import time
from shared.audit import audit
from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.smarttyre import verify
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete  # noqa: F401 (TRANSIENT: parte del contrato del módulo)
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import _find_id, resolve_or_heal
from shared.utils.clock import now_ms
from functions.bindings.bind_sensor import platform_bind_sensor
from functions.vehicles.create import _dajin_type

# Cuántas filas procesar por barrido y por tabla (para no pasarnos del timeout).
BATCH = 100

# Cool-off: NO tocar filas modificadas hace menos de esto. Evita que el cron corra sobre un
# snapshot viejo y pise/resucite una liga que un request de usuario acaba de cambiar. El
# barrido C además re-lee la fila justo antes de re-ligar y re-verifica el predicado local.
COOLOFF_MS = 120_000

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

    summary = {"resolved": 0, "deleted": 0, "guard_blocked": 0,
               "rebound": 0, "binding_pending": 0, "errors": 0,
               "verified": 0, "phantom_cleared": 0, "phantom_healed": 0,
               "verify_skipped": 0}

    for cfg in ASSETS:
        table = t(cfg["table"])
        _sweep_registering(db, st, table, cfg, summary)
        _sweep_pending_deletes(db, st, table, cfg, summary)
        _sweep_phantom_ids(db, st, table, cfg, summary)

    # C. Ligas divergentes (activos ya sincronizados cuya relación no está en la plataforma).
    _sweep_qbox_bindings(db, st, summary)
    _sweep_tyre_bindings(db, st, summary)
    _sweep_sensor_bindings(db, st, summary)

    return {"status": "ok", **summary}


def _sweep_registering(db, st, table, cfg, summary):
    """A. Re-resuelve el daijin_id de los creates que quedaron sin sincronizar.

    Se selecciona por `daijin_id IS NULL`, no por `status = 'registering'`: el status es
    editable desde el PUT, y marcarlo a mano no puede sacar una fila del barrido. Lo
    único que prueba que un activo está sincronizado es tener id en la plataforma.

    El status solo se toca cuando la fila seguía en 'registering'. Si ya traía un status
    de negocio (una llanta 'used', un sensor 'inactive'), se le completa el id y se
    respeta lo que el usuario puso.
    """
    # order="DESC": lo más reciente primero. El selector por `daijin_id IS NULL` abarca
    # también filas viejas que nunca tuvieron id y que quizá no existan en la plataforma;
    # con orden ascendente y un cupo de BATCH, ese rezago se comería la corrida entera y
    # un create atorado de hoy no se reconciliaría nunca.
    rows = get_where(db, table, "daijin_id IS NULL AND is_deleted = 0", [], BATCH,
                     order="DESC")
    for r in rows:
        try:
            found = _find_id(st, cfg["list_path"], cfg["key"](r))
            if found is None:
                continue  # aún no aparece en la plataforma; se reintenta la próxima corrida
            fields = {"daijin_id": found, "updated_at": now_ms()}
            if r.get("status") == "registering":
                fields["status"] = cfg["active"]
            update(db, table, r["id"], fields)
            db.commit()
            summary["resolved"] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _sweep_pending_deletes(db, st, table, cfg, summary):
    """B. Reintenta el borrado en la plataforma de los que quedaron is_deleted=1 con daijin_id."""
    rows = get_where(db, table, "is_deleted = 1 AND daijin_id IS NOT NULL", [], BATCH)
    for r in rows:
        try:
            status, msg = attempt_delete(cfg["resource"], str(r["daijin_id"]))
            if status == DONE:
                _clear_daijin(db, table, r, cfg["table"])
                summary["deleted"] += 1
            elif status == GUARD:
                # ¿"ya no existe" (idempotente) o un guard real (ej. 531 con sensor)?
                if _find_id(st, cfg["list_path"], cfg["key"](r)) is None:
                    _clear_daijin(db, table, r, cfg["table"])  # ya no estaba allá
                    summary["deleted"] += 1
                else:
                    summary["guard_blocked"] += 1  # necesita acción manual (desvincular)
            # TRANSIENT -> se deja para la próxima corrida
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _clear_daijin(db, table, rec, resource):
    """Cierra el borrado: suelta el id de la plataforma y libera la llave natural.

    La llave se libera AQUÍ y no cuando el borrado se marcó pendiente, porque este
    mismo barrido la necesita para buscar el activo allá (`cfg["key"]`). Si se hubiera
    liberado antes, la búsqueda no encontraría nada y "no está" se confundiría con
    "ya se borró".
    """
    update(db, table, rec["id"], {
        "daijin_id": None, "updated_at": now_ms(),
        **liberar_llave_natural(rec, resource),
    })
    db.commit()



# --------------------------------------------------------------------------- D. Id fantasma
# Cuántas corridas del cron dan una vuelta completa a la flota. El cron corre cada
# 5 minutos, así que 288 corridas son 24 horas: cada activo sincronizado se revisa
# una vez al día, y cada corrida toca solo 1/288 de las filas (unas 38 de 11,000).
# No hace falta guardar estado entre corridas ni una columna nueva: la rebanada sale
# del reloj y del id.
VERIFY_PERIOD = int(os.getenv("VERIFY_PERIOD_RUNS", "288"))
VERIFY_MAX = int(os.getenv("VERIFY_MAX_ROWS", "80"))


def _rebanada_actual(ahora=None):
    """Qué rebanada de ids toca revisar en esta corrida (0 .. VERIFY_PERIOD-1)."""
    t0 = ahora if ahora is not None else time.time()
    return int(t0 // 300) % VERIFY_PERIOD


def _sweep_phantom_ids(db, st, table, cfg, summary):
    """D. Revisa que los activos YA sincronizados sigan existiendo en la plataforma.

    Los otros barridos alcanzan a un activo a través de lo que tiene armado: la llanta
    porque está montada, el sensor porque está en una llanta, el Qbox porque está en una
    unidad. Un activo que no está armado con nada no tiene por dónde ser alcanzado, así
    que su `daijin_id` no se vuelve a comprobar nunca.

    Medido en producción: 2,073 activos con id guardado que nadie revisaba, el 19 % del
    total. Son los que están en inventario: sensor sin montar, llanta desmontada, unidad
    sin Qbox. Si ese id quedó mal o el activo se borró en la plataforma, nadie se entera
    hasta que alguien intenta usarlo, y ahí falla sin explicación.

    Cuando el id resulta ser fantasma NO se re-registra aquí: se limpia el `daijin_id` y
    se devuelve la fila a 'registering', que es su estado real. El barrido A la recoge en
    la corrida siguiente y hace el registro con el camino que ya existe y ya está probado.

    Un fallo de lectura NUNCA se trata como ausencia: `confirm_on_platform` distingue
    "la plataforma dice que no está" de "no se pudo preguntar", y solo el primero limpia.
    """
    rebanada = _rebanada_actual()
    # Cool-off, igual que los barridos de ligas: no tocar filas que un usuario acaba
    # de modificar. Importa especialmente aquí porque la plataforma tarda en dejar
    # ver lo recién insertado — por eso el alta hace GET despues del POST. Sin esta
    # guarda, una llanta creada hace 30 segundos podría dar "no está" y el barrido le
    # borraría el id de un activo perfectamente sano. Se recupera sola en la corrida
    # siguiente, pero mientras tanto la pantalla la muestra sin sincronizar.
    rows = get_where(
        db, table,
        f"daijin_id IS NOT NULL AND is_deleted = 0 AND (id %% {VERIFY_PERIOD}) = %s "
        "AND updated_at < %s",
        [rebanada, now_ms() - COOLOFF_MS], VERIFY_MAX)

    for r in rows:
        try:
            real = confirm_on_platform(r, cfg["table"], client=st)
        except NotOnPlatform:
            # El id apunta a algo que ya no existe. Se devuelve la fila al estado que
            # de verdad tiene, y el barrido A la vuelve a registrar.
            update(db, table, r["id"], {
                "daijin_id": None, "status": "registering", "updated_at": now_ms(),
            })
            db.commit()
            summary["phantom_cleared"] = summary.get("phantom_cleared", 0) + 1
            continue
        except PlatformUnavailable:
            # No se pudo preguntar. Eso no prueba nada: se reintenta en su próxima vuelta.
            summary["verify_skipped"] = summary.get("verify_skipped", 0) + 1
            continue
        except Exception:
            summary["errors"] += 1
            continue

        if real and str(real) != str(r.get("daijin_id")):
            # Existe, pero con otro id: el local estaba mal escrito. Manda el de allá.
            update(db, table, r["id"], {"daijin_id": real, "updated_at": now_ms()})
            db.commit()
            summary["phantom_healed"] = summary.get("phantom_healed", 0) + 1
        else:
            summary["verified"] = summary.get("verified", 0) + 1

# --------------------------------------------------------------------------- C. Ligas divergentes
def _heal_vehicle(db, st, unit, catalog):
    """Asegura que el vehículo exista en la plataforma; lo re-crea si es fantasma.

    Devuelve el daijin_id real del vehículo (actualizándolo local si cambió), o None si no
    se pudo resolver. La llave natural (licensePlateNumber == id local) es única global, así
    que re-crear nunca duplica.
    """
    is_tractor, model_id = _dajin_type(catalog)
    try:
        real_id, changed = resolve_or_heal(
            st, stored_id=unit.get("daijin_id"),
            list_path="/smartyre/openapi/vehicle/list",
            list_filter={"licensePlateNumber": str(unit["id"])},
            insert_path="/smartyre/openapi/vehicle/insert",
            insert_payload={
                "licensePlateNumber": str(unit["id"]), "isTractor": is_tractor,
                "modelId": model_id, "axleTypeId": str(catalog.get("d_id") or ""),
                "orgId": DAJIN_ORG_ID})
    except Exception:
        return None
    if changed:
        update(db, t("units"), unit["id"], {"daijin_id": real_id, "updated_at": now_ms()})
        db.commit()
    return real_id


def _sweep_qbox_bindings(db, st, summary):
    """C1. Re-liga el Qbox de unidades activas cuyo vehículo quedó suelto en la plataforma.

    Si el Qbox es fantasma (ya no existe en la plataforma) se re-crea antes de re-ligar.
    Siempre se verifica por read-back: si tras re-ligar la plataforma sigue sin reflejarlo,
    se deja pendiente para la próxima corrida (nunca se marca como sano sin confirmar).
    """
    rows = get_where(
        db, t("units"),
        "tbox_id IS NOT NULL AND daijin_id IS NOT NULL AND status = %s "
        "AND (updated_at IS NULL OR updated_at < %s) AND (is_deleted IS NULL OR is_deleted = 0)",
        ["active", now_ms() - COOLOFF_MS], BATCH,
    )
    for u in rows:
        try:
            # Re-leer la fila: pudo cambiar desde el snapshot (un unbind concurrente). No
            # actuar sobre datos viejos -> no resucitar una liga que el usuario quitó.
            u = get_by_id(db, t("units"), u["id"])
            if not u or not u.get("tbox_id") or u.get("status") != "active":
                continue
            tbox = get_by_id(db, t("tboxes"), u["tbox_id"])
            if not tbox or not tbox.get("tboxCode"):
                continue
            code = tbox["tboxCode"]
            if verify.tbox_bound(st, plate=u["id"], tbox_code=code):
                continue  # ya está bien ligado
            # (a) asegurar que el Qbox exista en la plataforma (re-crear si es fantasma).
            real_id, changed = resolve_or_heal(
                st, stored_id=tbox.get("daijin_id"),
                list_path="/smartyre/openapi/tbox/list", list_filter={"tboxCode": code},
                insert_path="/smartyre/openapi/tbox/insert", insert_payload={"tboxCode": code})
            if changed:
                update(db, t("tboxes"), tbox["id"], {"daijin_id": real_id, "updated_at": now_ms()})
                db.commit()
            # (b) asegurar que el VEHÍCULO exista (re-crear si es fantasma) y re-mandar el
            #     bind (vehicle/update con el tboxCode).
            catalog = get_by_id(db, "unit_catalog", u.get("unit_catalog_id")) or {}
            veh_daijin = _heal_vehicle(db, st, u, catalog)
            if not veh_daijin:
                summary["errors"] += 1
                continue
            is_tractor, model_id = _dajin_type(catalog)
            st.post("/smartyre/openapi/vehicle/update", {
                "id": veh_daijin, "isTractor": is_tractor,
                "licensePlateNumber": str(u["id"]),
                "axleTypeId": str(catalog.get("d_id") or ""), "modelId": model_id,
                "orgId": DAJIN_ORG_ID, "tboxCode": code})
            # (c) verificar y auditar.
            bound = verify.tbox_bound(st, plate=u["id"], tbox_code=code)
            audit(db, None, None, action="reconcile", asset_type="tbox", asset_id=tbox["id"],
                  natural_key=code, company_id=u.get("company_id"), daijin_id=real_id,
                  result=("success" if bound else "pending"), changes={"unit_id": u["id"]},
                  error=(None if bound else "qbox re-bind no confirmado en la plataforma"))
            summary["rebound" if bound else "binding_pending"] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _sweep_tyre_bindings(db, st, summary):
    """C2. Re-monta en la plataforma las llantas montadas localmente que quedaron sueltas allá."""
    rows = get_where(
        db, t("tires"),
        "unit_id IS NOT NULL AND is_mounted = 1 AND daijin_id IS NOT NULL "
        "AND (updated_at IS NULL OR updated_at < %s) AND (is_deleted IS NULL OR is_deleted = 0)",
        [now_ms() - COOLOFF_MS], BATCH,
    )
    for ti in rows:
        try:
            # Re-leer: un unbind/desmontaje concurrente pudo cambiarla desde el snapshot.
            ti = get_by_id(db, t("tires"), ti["id"])
            if not ti or not ti.get("unit_id") or not ti.get("is_mounted"):
                continue
            unit = get_by_id(db, t("units"), ti["unit_id"])
            if not unit or not unit.get("daijin_id"):
                continue
            if verify.tyre_on_vehicle(st, tyre_code=ti["id"], plate=ti["unit_id"]):
                continue
            # Asegurar que el vehículo exista (re-crear si es fantasma) antes de re-montar.
            catalog = get_by_id(db, "unit_catalog", unit.get("unit_catalog_id")) or {}
            veh_daijin = _heal_vehicle(db, st, unit, catalog)
            if not veh_daijin:
                summary["errors"] += 1
                continue
            st.post("/smartyre/openapi/vehicle/tyre/bind", {
                "vehicleId": veh_daijin, "tyreCode": str(ti["id"]),
                "axleIndex": ti.get("axle_index"), "wheelIndex": ti.get("wheel_index")})
            bound = verify.tyre_on_vehicle(st, tyre_code=ti["id"], plate=ti["unit_id"])
            audit(db, None, None, action="reconcile", asset_type="tire", asset_id=ti["id"],
                  natural_key=ti.get("folio"), company_id=ti.get("company_id"),
                  daijin_id=ti.get("daijin_id"),
                  result=("success" if bound else "pending"), changes={"unit_id": ti["unit_id"]},
                  error=(None if bound else "re-montaje de llanta no confirmado en la plataforma"))
            summary["rebound" if bound else "binding_pending"] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1


def _sweep_sensor_bindings(db, st, summary):
    """C3. Re-asocia en la plataforma los sensores de llantas montadas que quedaron sueltos allá."""
    rows = get_where(
        db, t("tires"),
        "sensor_id IS NOT NULL AND unit_id IS NOT NULL AND is_mounted = 1 AND daijin_id IS NOT NULL "
        "AND (updated_at IS NULL OR updated_at < %s) AND (is_deleted IS NULL OR is_deleted = 0)",
        [now_ms() - COOLOFF_MS], BATCH,
    )
    for ti in rows:
        try:
            # Re-leer: un unbind de sensor concurrente pudo cambiarla desde el snapshot.
            ti = get_by_id(db, t("tires"), ti["id"])
            if not ti or not ti.get("sensor_id") or not ti.get("unit_id") or not ti.get("is_mounted"):
                continue
            sensor = get_by_id(db, t("sensors"), ti["sensor_id"])
            unit = get_by_id(db, t("units"), ti["unit_id"])
            if not (sensor and sensor.get("sensorCode") and unit and unit.get("daijin_id")):
                continue
            if verify.sensor_on_tyre(st, sensor_code=sensor["sensorCode"], tyre_code=ti["id"]):
                continue
            platform_bind_sensor(
                st, tyre_code=ti["id"], axle=ti.get("axle_index"),
                wheel=ti.get("wheel_index"), sensor_code=sensor["sensorCode"],
                vehicle_id=unit["daijin_id"])
            bound = verify.sensor_on_tyre(st, sensor_code=sensor["sensorCode"], tyre_code=ti["id"])
            audit(db, None, None, action="reconcile", asset_type="sensor", asset_id=sensor["id"],
                  natural_key=sensor.get("sensorCode"), company_id=sensor.get("company_id"),
                  daijin_id=sensor.get("daijin_id"),
                  result=("success" if bound else "pending"), changes={"tire_id": ti["id"]},
                  error=(None if bound else "re-asociación de sensor no confirmada en la plataforma"))
            summary["rebound" if bound else "binding_pending"] += 1
        except Exception:
            db.rollback()
            summary["errors"] += 1

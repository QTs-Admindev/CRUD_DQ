from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, soft_delete, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending_delete


def handler(event, context):
    # DELETE /tires/{id} -> cascade so the user never has to unbind manually:
    #   1) free its sensor (stays in inventory, NOT deleted)
    #   2) unmount it from its vehicle
    #   3) delete the tyre remotely (basic-api) + soft-delete local.
    # The remote delete GUARDs a tyre that still has a sensor/vehicle, so the
    # unbinds MUST happen first.
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    db = get_db()
    rec = get_by_id(db, t("tires"), rid)
    if not rec:
        return error(404, "Llanta no encontrada")
    if rec.get("is_deleted"):
        return ok(rec)

    unit = get_by_id(db, t("units"), rec["unit_id"]) if rec.get("unit_id") else None

    # 1) Free the sensor (it survives, in inventory).
    if rec.get("sensor_id"):
        sensor = get_by_id(db, t("sensors"), rec["sensor_id"])
        try:
            st = SmartTyreClient()
            st.post("/smartyre/openapi/tyre/sensor/unbind", {
                "tyreCode": str(rid),
                "vehicleId": unit.get("daijin_id") if unit else None,
                "axleIndex": rec.get("axle_index"),
                "wheelIndex": rec.get("wheel_index"),
                "sensorCode": sensor.get("sensorCode") if sensor else None,
            })
        except Exception:
            return error(502, "No se pudo liberar el sensor, intenta de nuevo")
        try:
            update(db, t("tires"), rid, {"sensor_id": None, "updated_at": now_ms()})
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (liberar sensor): {e}")

    # 2) Unmount from its vehicle.
    if rec.get("unit_id"):
        if unit and unit.get("daijin_id"):
            try:
                st = SmartTyreClient()
                st.post("/smartyre/openapi/vehicle/tyre/unbind", {
                    "vehicleId": unit.get("daijin_id"),
                    "tyreCode": str(rid),
                })
            except Exception:
                return error(502, "No se pudo desmontar la llanta, intenta de nuevo")
        try:
            update(db, t("tires"), rid, {
                "unit_id": None, "is_mounted": 0, "axle_index": None,
                "wheel_index": None, "mount_position": None, "updated_at": now_ms(),
            })
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (desmontar llanta): {e}")

    # 3) Delete the tyre. Platform-first (basic-api) then soft-delete local.
    daijin_id = rec.get("daijin_id")
    if daijin_id:
        status, msg = attempt_delete("tyre", str(daijin_id))
        if status == GUARD:
            return error(409, "No se pudo completar el borrado")
    else:
        status, msg = DONE, None

    try:
        if status == TRANSIENT:
            rec = soft_delete(db, t("tires"), rid)
            db.commit()
            return pending_delete(rec, msg)
        rec = update(db, t("tires"), rid, {
            "is_deleted": 1, "daijin_id": None, "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (delete llanta, daijin_id={daijin_id}): {e}")

import json

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


def handler(event, context):
    # path: /tires/{id}/sensors/unbind  -> id = llanta local. Desvincula el sensor actual.
    try:
        tire_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    db = get_db()
    tire = get_by_id(db, t("tires"), tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")
    sensor_id = tire.get("sensor_id")
    if not sensor_id:
        return error(409, "La llanta no tiene un sensor vinculado")

    sensor = get_by_id(db, t("sensors"), sensor_id)
    unit = get_by_id(db, t("units"), tire["unit_id"]) if tire.get("unit_id") else None

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/tyre/sensor/unbind", {
            "tyreCode": str(tire_id),
            "vehicleId": unit.get("daijin_id") if unit else None,
            "axleIndex": tire.get("axle_index"),
            "wheelIndex": tire.get("wheel_index"),
            "sensorCode": sensor.get("sensorCode") if sensor else None,
        })
    except Exception as e:
        return error(502, "No se pudo desvincular el sensor, intenta de nuevo")

    try:
        rec = update(db, t("tires"), tire_id, {
            "sensor_id": None,
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (unbind sensor local): {e}")

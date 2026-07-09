import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class BindSensorRequest(BaseModel):
    sensor_id: int
    axle_index: int | None = None
    wheel_index: int | None = None


def handler(event, context):
    # path: /tires/{id}/sensors/bind  -> id = llanta local
    try:
        tire_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")
    try:
        body = BindSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    tire = get_by_id(db, t("tires"), tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")
    if tire.get("sensor_id"):
        return error(409, "La llanta ya tiene un sensor vinculado")
    # Binding sensor->tire needs the tire's vehicle (vehicleId in the platform).
    if not tire.get("unit_id"):
        return error(409, "La llanta no está montada en un vehículo")
    if not tire.get("daijin_id"):
        return error(409, "La llanta aún no está sincronizada con la plataforma")
    unit = get_by_id(db, t("units"), tire["unit_id"])
    if not unit or not unit.get("daijin_id"):
        return error(409, "El vehículo de la llanta no está sincronizado con la plataforma")
    sensor = get_by_id(db, t("sensors"), body.sensor_id)
    if not sensor:
        return error(404, "Sensor no encontrado")

    axle = body.axle_index if body.axle_index is not None else tire.get("axle_index")
    wheel = body.wheel_index if body.wheel_index is not None else tire.get("wheel_index")

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/tyre/sensor/bind", {
            "tyreCode": str(tire_id),
            "axleIndex": axle,
            "wheelIndex": wheel,
            "sensorCode": sensor["sensorCode"],
            "vehicleId": unit["daijin_id"],
        })
    except Exception as e:
        return error(502, f"Error de la plataforma (bind sensor): {e}")

    # Local: la relación sensor<->llanta vive en tires.sensor_id.
    try:
        rec = update(db, t("tires"), tire_id, {
            "sensor_id": body.sensor_id,
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (bind sensor local): {e}")

import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class UnbindSensorRequest(BaseModel):
    sensor_id: int


def handler(event, context):
    try:
        tire_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    try:
        body = UnbindSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    tire = get_by_id(db, "tires", tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")

    sensor = get_by_id(db, "sensors", body.sensor_id)
    if not sensor:
        return error(404, "Sensor no encontrado")

    if sensor.get("tire_id") != tire_id:
        return error(409, "El sensor no está vinculado a esta llanta")

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/tyre/sensor/unbind", {
            "tyreId": tire["smarttyre_id"],
            "sensorId": sensor["smarttyre_id"],
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        record = update(db, "sensors", body.sensor_id, {"tire_id": None})
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

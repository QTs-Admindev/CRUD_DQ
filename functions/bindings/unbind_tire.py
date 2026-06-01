import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class UnbindTireRequest(BaseModel):
    tire_id: int


def handler(event, context):
    try:
        vehicle_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    try:
        body = UnbindTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    vehicle = get_by_id(db, "vehicles", vehicle_id)
    if not vehicle:
        return error(404, "Vehículo no encontrado")

    tire = get_by_id(db, "tires", body.tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")

    if not tire.get("is_mounted") or tire.get("vehicle_id") != vehicle_id:
        return error(409, "La llanta no está montada en este vehículo")

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/tyre/unbind", {
            "vehicleId": vehicle["smarttyre_id"],
            "tyreId": tire["smarttyre_id"],
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        record = update(db, "tires", body.tire_id, {
            "vehicle_id": None,
            "is_mounted": 0,
            "axle_index": None,
            "wheel_index": None,
            "mount_position": None,
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class BindTireRequest(BaseModel):
    tire_id: int
    axle_index: int
    wheel_index: int
    mount_position: int


def handler(event, context):
    try:
        vehicle_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    try:
        body = BindTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    vehicle = get_by_id(db, "vehicles", vehicle_id)
    if not vehicle:
        return error(404, "Vehículo no encontrado")

    tire = get_by_id(db, "tires", body.tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")

    if tire.get("is_mounted"):
        return error(409, "La llanta ya está montada en otro vehículo")

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/tyre/bind", {
            "vehicleId": vehicle["smarttyre_id"],
            "tyreId": tire["smarttyre_id"],
            "axleIndex": body.axle_index,
            "wheelIndex": body.wheel_index,
            "mountPosition": body.mount_position,
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        record = update(db, "tires", body.tire_id, {
            "vehicle_id": vehicle_id,
            "is_mounted": 1,
            "axle_index": body.axle_index,
            "wheel_index": body.wheel_index,
            "mount_position": body.mount_position,
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class UpdateVehicleRequest(BaseModel):
    unit_catalog_id: int | None = None
    unit_identifier: str | None = None
    is_tractor: bool | None = None
    status: str | None = None


def handler(event, context):
    try:
        vehicle_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    try:
        body = UpdateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    vehicle = get_by_id(db, "vehicles", vehicle_id)
    if not vehicle:
        return error(404, "Vehículo no encontrado")

    smarttyre_payload = {}
    if body.unit_catalog_id is not None:
        smarttyre_payload["unitCatalogId"] = body.unit_catalog_id
    if body.unit_identifier is not None:
        smarttyre_payload["unitIdentifier"] = body.unit_identifier
    if body.is_tractor is not None:
        smarttyre_payload["isTractor"] = 1 if body.is_tractor else 0

    if smarttyre_payload:
        smarttyre_payload["id"] = vehicle["smarttyre_id"]
        try:
            st = SmartTyreClient()
            st.post("/smartyre/openapi/vehicle/update", smarttyre_payload)
        except Exception as e:
            return error(502, f"SmartTyre error: {e}")

    mysql_payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if body.is_tractor is not None:
        mysql_payload["is_tractor"] = 1 if body.is_tractor else 0

    if not mysql_payload:
        return ok(vehicle)

    try:
        record = update(db, "vehicles", vehicle_id, mysql_payload)
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

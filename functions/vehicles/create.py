import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import insert
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class CreateVehicleRequest(BaseModel):
    license_plate: str
    company_id: int
    unit_catalog_id: int
    unit_identifier: str
    is_tractor: bool = False


def handler(event, context):
    try:
        body = CreateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/insert", {
            "licensePlate": body.license_plate,
            "companyId": body.company_id,
            "unitCatalogId": body.unit_catalog_id,
            "unitIdentifier": body.unit_identifier,
            "isTractor": 1 if body.is_tractor else 0,
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        resp = st.get("/smartyre/openapi/vehicle/list", {"licensePlate": body.license_plate})
        smarttyre_id = resp["records"][0]["id"]
    except Exception as e:
        return error(502, f"SmartTyre ID lookup failed: {e}")

    try:
        db = get_db()
        record = insert(db, "vehicles", {
            "license_plate": body.license_plate,
            "company_id": body.company_id,
            "unit_catalog_id": body.unit_catalog_id,
            "unit_identifier": body.unit_identifier,
            "is_tractor": 1 if body.is_tractor else 0,
            "smarttyre_id": smarttyre_id,
            "status": "registering",
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (SmartTyre ID={smarttyre_id}): {e}")

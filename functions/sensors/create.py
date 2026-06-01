import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.db.connection import get_db
from shared.db.ops import insert
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok
from shared.utils.validators import validate_hex12


class CreateSensorRequest(BaseModel):
    sensor_code: str
    company_id: int

    @field_validator("sensor_code")
    @classmethod
    def check_sensor_code(cls, v: str) -> str:
        return validate_hex12(v, "sensor_code")


def handler(event, context):
    try:
        body = CreateSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/sensor/insert", {
            "sensorCode": body.sensor_code,
            "companyId": body.company_id,
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        resp = st.get("/smartyre/openapi/sensor/list", {"sensorCode": body.sensor_code})
        smarttyre_id = resp["records"][0]["id"]
    except Exception as e:
        return error(502, f"SmartTyre ID lookup failed: {e}")

    try:
        db = get_db()
        record = insert(db, "sensors", {
            "sensor_code": body.sensor_code,
            "company_id": body.company_id,
            "smarttyre_id": smarttyre_id,
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (SmartTyre ID={smarttyre_id}): {e}")

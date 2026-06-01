import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.db.connection import get_db
from shared.db.ops import insert
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok
from shared.utils.validators import validate_hex12


class CreateTboxRequest(BaseModel):
    tbox_code: str
    company_id: int

    @field_validator("tbox_code")
    @classmethod
    def check_tbox_code(cls, v: str) -> str:
        return validate_hex12(v, "tbox_code")


def handler(event, context):
    try:
        body = CreateTboxRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/tbox/insert", {
            "tboxCode": body.tbox_code,
            "companyId": body.company_id,
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        resp = st.get("/smartyre/openapi/tbox/list", {"tboxCode": body.tbox_code})
        smarttyre_id = resp["records"][0]["id"]
    except Exception as e:
        return error(502, f"SmartTyre ID lookup failed: {e}")

    try:
        db = get_db()
        record = insert(db, "tboxes", {
            "tbox_code": body.tbox_code,
            "company_id": body.company_id,
            "smarttyre_id": smarttyre_id,
            "status": "registering",
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (SmartTyre ID={smarttyre_id}): {e}")

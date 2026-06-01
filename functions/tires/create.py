import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import insert
from shared.smarttyre.client import SmartTyreClient
from shared.utils.response import error, ok


class CreateTireRequest(BaseModel):
    tyre_code: str
    company_id: int
    prefix: str = ""
    folio: str = ""


def handler(event, context):
    try:
        body = CreateTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/tyre/insert", {
            "tyreCode": body.tyre_code,
            "companyId": body.company_id,
        })
    except Exception as e:
        return error(502, f"SmartTyre error: {e}")

    try:
        resp = st.get("/smartyre/openapi/tyre/list", {"tyreCode": body.tyre_code})
        smarttyre_id = resp["records"][0]["id"]
    except Exception as e:
        return error(502, f"SmartTyre ID lookup failed: {e}")

    try:
        db = get_db()
        record = insert(db, "tires", {
            "tyre_code": body.tyre_code,
            "company_id": body.company_id,
            "prefix": body.prefix,
            "folio": body.folio,
            "smarttyre_id": smarttyre_id,
            "is_mounted": 0,
        })
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (SmartTyre ID={smarttyre_id}): {e}")

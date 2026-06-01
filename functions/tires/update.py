import json

from pydantic import BaseModel, ValidationError

from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.response import error, ok


class UpdateTireRequest(BaseModel):
    prefix: str | None = None
    folio: str | None = None


def handler(event, context):
    try:
        tire_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    try:
        body = UpdateTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    tire = get_by_id(db, "tires", tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")

    mysql_payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not mysql_payload:
        return ok(tire)

    try:
        record = update(db, "tires", tire_id, mysql_payload)
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

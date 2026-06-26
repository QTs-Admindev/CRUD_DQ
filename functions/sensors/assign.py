import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import exists, get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class AssignRequest(BaseModel):
    company_id: int


def handler(event, context):
    # POST /sensors/{id}/assign -> move a sensor from inventory to a company.
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de sensor inválido")
    try:
        body = AssignRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    rec = get_by_id(db, t("sensors"), rid)
    if not rec:
        return error(404, "Sensor no encontrado")
    # Cannot reassign while bound to a tire; unbind first.
    if exists(db, t("tires"), {"sensor_id": rid, "is_deleted": 0}):
        return error(409, "El sensor está vinculado a una llanta; desvincúlalo primero")

    try:
        rec = update(db, t("sensors"), rid, {"company_id": body.company_id, "updated_at": now_ms()})
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (assign sensor): {e}")

import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, insert
from shared.locations import es_duplicado, limpiar_nombre
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class CreateSiteRequest(BaseModel):
    # Dueña de la sede. La admin (2) puede crear sedes para cualquier compañía y
    # también las suyas; el FE de una compañía cliente solo manda la propia.
    company_id: int
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return limpiar_nombre(v)


def handler(event, context):
    # POST /sites -> nueva sede de una compañía.
    try:
        body = CreateSiteRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    if not get_by_id(db, "companies", body.company_id):
        return error(422, "company_id no existe")

    try:
        ts = now_ms()
        rec = insert(db, t("sites"), {
            "company_id": body.company_id, "name": body.name,
            "created_at": ts, "updated_at": ts,
        })
        db.commit()
    except Exception as e:
        db.rollback()
        if es_duplicado(e):
            return error(409, f"Ya existe una sede llamada «{body.name}» en esta compañía")
        return error(500, f"DB error (crear sede): {e}")

    audit(db, event, context, action="create", asset_type="site", asset_id=rec["id"],
          natural_key=body.name, company_id=body.company_id, result="success")
    return ok(rec)

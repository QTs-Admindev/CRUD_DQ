import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.locations import compania_que_pide, en_alcance, es_duplicado, limpiar_nombre
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class UpdateSiteRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return limpiar_nombre(v)


def handler(event, context):
    # PUT /sites/{id}?company_id=.. -> renombrar la sede. Una sede no cambia de
    # compañía: sus unidades y almacenes son de esa compañía.
    try:
        site_id = int((event.get("pathParameters") or {})["id"])
        quien = compania_que_pide(event)
    except (KeyError, TypeError, ValueError):
        return error(400, "id de sede o company_id inválido")
    try:
        body = UpdateSiteRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    sede = get_by_id(db, t("sites"), site_id)
    if not sede or not en_alcance(quien, sede["company_id"]):
        return error(404, "Sede no encontrada")
    if sede["name"] == body.name:
        return ok(sede)

    try:
        rec = update(db, t("sites"), site_id, {"name": body.name, "updated_at": now_ms()})
        db.commit()
    except Exception as e:
        db.rollback()
        if es_duplicado(e):
            return error(409, f"Ya existe una sede llamada «{body.name}» en esta compañía")
        return error(500, f"DB error (renombrar sede): {e}")

    audit(db, event, context, action="update", asset_type="site", asset_id=site_id,
          natural_key=body.name, company_id=sede["company_id"], result="success",
          changes={"name": [sede["name"], body.name]})
    return ok(rec)

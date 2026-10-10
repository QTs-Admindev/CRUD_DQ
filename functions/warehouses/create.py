import json
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, insert
from shared.locations import es_duplicado, limpiar_nombre
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class CreateWarehouseRequest(BaseModel):
    company_id: int
    name: str
    # Obligatoria: todo almacén vive en una sede de su compañía.
    site_id: int
    type: Literal["general", "scrap", "retreading"] = "general"

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return limpiar_nombre(v)


def handler(event, context):
    # POST /warehouses -> nuevo almacén en una sede de la compañía.
    try:
        body = CreateWarehouseRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    if not get_by_id(db, "companies", body.company_id):
        return error(422, "company_id no existe")
    sede = get_by_id(db, t("sites"), body.site_id)
    if not sede or sede["company_id"] != body.company_id:
        return error(422, "La sede no existe en esa compañía")

    try:
        ts = now_ms()
        rec = insert(db, t("warehouses"), {
            "company_id": body.company_id, "name": body.name, "type": body.type,
            "site_id": body.site_id,
            "created_at": ts, "updated_at": ts,
        })
        db.commit()
    except Exception as e:
        db.rollback()
        if es_duplicado(e):
            return error(409, f"Ya existe un almacén llamado «{body.name}» en esa sede")
        return error(500, f"DB error (crear almacén): {e}")

    audit(db, event, context, action="create", asset_type="warehouse", asset_id=rec["id"],
          natural_key=body.name, company_id=body.company_id, result="success",
          payload={"site_id": body.site_id, "type": body.type})
    return ok(rec)

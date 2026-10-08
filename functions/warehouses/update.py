import json
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.locations import compania_que_pide, en_alcance, es_duplicado, limpiar_nombre
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class UpdateWarehouseRequest(BaseModel):
    name: str | None = None
    type: Literal["general", "scrap", "retreading"] | None = None
    # Mandar site_id: null saca el almacén de su sede; no mandarlo lo deja igual.
    site_id: int | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return None if v is None else limpiar_nombre(v)


def handler(event, context):
    # PUT /warehouses/{id}?company_id=.. -> renombrar, cambiar tipo o cambiar de
    # sede (dentro de la misma compañía). Un almacén no cambia de compañía.
    try:
        wid = int((event.get("pathParameters") or {})["id"])
        quien = compania_que_pide(event)
    except (KeyError, TypeError, ValueError):
        return error(400, "id de almacén o company_id inválido")
    try:
        body = UpdateWarehouseRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    alm = get_by_id(db, t("warehouses"), wid)
    if not alm or not en_alcance(quien, alm["company_id"]):
        return error(404, "Almacén no encontrado")

    cambios: dict = {}
    if body.name is not None and body.name != alm["name"]:
        cambios["name"] = body.name
    if body.type is not None and body.type != alm.get("type"):
        cambios["type"] = body.type
    if "site_id" in body.model_fields_set and body.site_id != alm.get("site_id"):
        sede = None
        if body.site_id is not None:
            sede = get_by_id(db, t("sites"), body.site_id)
            if not sede or sede["company_id"] != alm["company_id"]:
                return error(422, "La sede no existe en la compañía del almacén")
        cambios["site_id"] = body.site_id
        cambios["yard"] = sede["name"] if sede else ""
    if not cambios:
        return ok(alm)

    try:
        rec = update(db, t("warehouses"), wid, {**cambios, "updated_at": now_ms()})
        db.commit()
    except Exception as e:
        db.rollback()
        if es_duplicado(e):
            return error(409, "Ya existe un almacén con ese nombre en esa sede")
        return error(500, f"DB error (editar almacén): {e}")

    audit(db, event, context, action="update", asset_type="warehouse", asset_id=wid,
          natural_key=rec.get("name"), company_id=alm["company_id"], result="success",
          changes={k: [alm.get(k), v] for k, v in cambios.items()})
    return ok(rec)

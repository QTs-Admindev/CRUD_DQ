import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok
from functions.vehicles.create import _dajin_type


class BindTboxRequest(BaseModel):
    tbox_id: int


def handler(event, context):
    # path: /vehicles/{id}/tbox/bind  -> id = unidad local. Asigna un tbox al vehículo.
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")
    try:
        body = BindTboxRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")
    if not unit.get("daijin_id"):
        return error(409, "El vehículo aún no está sincronizado con Dajin")
    tbox = get_by_id(db, t("tboxes"), body.tbox_id)
    if not tbox:
        return error(404, "Tbox no encontrado")
    if not tbox.get("daijin_id"):
        return error(409, "El tbox aún no está sincronizado con Dajin")
    catalog = get_by_id(db, "unit_catalog", unit.get("unit_catalog_id"))
    if not catalog:
        return error(422, "unit_catalog del vehículo no encontrado")

    is_tractor, model_id = _dajin_type(catalog)

    # Dajin: vehicle/update reenvía los datos del vehículo + tboxId (daijin del tbox).
    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/update", {
            "id": unit["daijin_id"],
            "isTractor": is_tractor,
            "licensePlateNumber": str(unit_id),
            "axleTypeId": str(catalog.get("d_id") or ""),
            "modelId": model_id,
            "orgId": str(unit.get("company_id")),
            "tboxId": tbox["daijin_id"],
        })
    except Exception as e:
        return error(502, f"Dajin error (asignar tbox): {e}")

    try:
        rec = update(db, t("units"), unit_id, {"tbox_id": body.tbox_id, "updated_at": now_ms()})
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar tbox local): {e}")

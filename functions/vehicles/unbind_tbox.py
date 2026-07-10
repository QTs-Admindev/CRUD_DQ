import json

from shared.audit import audit
from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok
from functions.vehicles.create import _dajin_type


def handler(event, context):
    # path: /vehicles/{id}/tbox/unbind  -> id = unidad local. Quita el tbox del vehículo.
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")
    if not unit.get("tbox_id"):
        return error(409, "La unidad no tiene un Qbox asignado")
    if not unit.get("daijin_id"):
        return error(409, "El vehículo aún no está listo")
    catalog = get_by_id(db, "unit_catalog", unit.get("unit_catalog_id"))
    if not catalog:
        return error(422, "unit_catalog del vehículo no encontrado")

    is_tractor, model_id = _dajin_type(catalog)

    # Dajin: vehicle/update con tboxId vacío (desasocia el tbox).
    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/update", {
            "id": unit["daijin_id"],
            "isTractor": is_tractor,
            "licensePlateNumber": str(unit_id),
            "axleTypeId": str(catalog.get("d_id") or ""),
            "modelId": model_id,
            "orgId": DAJIN_ORG_ID,
            "tboxCode": "",
        })
    except Exception as e:
        return error(502, "No se pudo quitar el Qbox, intenta de nuevo")

    try:
        rec = update(db, t("units"), unit_id, {"tbox_id": None, "updated_at": now_ms()})
        db.commit()
        audit(db, event, context, action="unbind", asset_type="tbox",
              asset_id=unit.get("tbox_id"), company_id=unit.get("company_id"),
              result="success", changes={"unit_id": None})
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (quitar tbox local): {e}")

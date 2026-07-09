import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class BindTireRequest(BaseModel):
    tire_id: int
    axle_index: int
    wheel_index: int
    mount_position: int | None = None


def handler(event, context):
    # path: /vehicles/{id}/tires/bind  -> id = unidad local
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")
    try:
        body = BindTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")
    if not unit.get("daijin_id"):
        return error(409, "El vehículo aún no está listo")
    tire = get_by_id(db, t("tires"), body.tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")
    if tire.get("is_mounted"):
        return error(409, "La llanta ya está montada")
    if not tire.get("daijin_id"):
        return error(409, "La llanta aún no está lista")

    # Platform first: vehicleId = unit's daijin_id, tyreCode = local tire id.
    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/tyre/bind", {
            "vehicleId": unit["daijin_id"],
            "tyreCode": str(body.tire_id),
            "axleIndex": body.axle_index,
            "wheelIndex": body.wheel_index,
        })
    except Exception as e:
        return error(502, "No se pudo completar la vinculación de la llanta, intenta de nuevo")

    # Local: reflejar la relación llanta -> unidad.
    try:
        rec = update(db, t("tires"), body.tire_id, {
            "unit_id": unit_id,
            "is_mounted": 1,
            "axle_index": body.axle_index,
            "wheel_index": body.wheel_index,
            "mount_position": body.mount_position,
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (bind tire local): {e}")

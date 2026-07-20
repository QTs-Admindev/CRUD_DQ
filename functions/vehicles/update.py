import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class UpdateVehicleRequest(BaseModel):
    unit_identifier: str


def handler(event, context):
    # PUT /vehicles/{id} — solo se permite editar el unit_identifier.
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")
    try:
        body = UpdateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")

    # unit_identifier es campo LOCAL (Dajin usa licensePlateNumber=id local) -> no toca Dajin.
    try:
        rec = update(db, t("units"), unit_id, {
            "unit_identifier": body.unit_identifier,
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        if "Duplicate" in str(e):
            return error(409, "Ya existe una unidad con ese identificador")
        return error(500, f"DB error (update unit): {e}")

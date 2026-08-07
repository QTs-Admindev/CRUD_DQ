import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.db.lock import with_asset_lock
from shared.smarttyre import verify
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending


class UnbindTireRequest(BaseModel):
    tire_id: int


@with_asset_lock(lambda e: "unit:" + str((e.get("pathParameters") or {}).get("id")))
def handler(event, context):
    # path: /vehicles/{id}/tires/unbind  -> id = unidad local
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")
    try:
        body = UnbindTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")
    tire = get_by_id(db, t("tires"), body.tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")
    if not tire.get("is_mounted") or tire.get("unit_id") != unit_id:
        return error(409, "La llanta no está montada en este vehículo")

    try:
        st = SmartTyreClient()
        st.post("/smartyre/openapi/vehicle/tyre/unbind", {
            "vehicleId": unit.get("daijin_id"),
            "tyreCode": str(body.tire_id),
        })
    except Exception as e:
        return error(502, "No se pudo desmontar la llanta, intenta de nuevo")

    # Confirmar por read-back que la llanta REALMENTE quedó desmontada del vehículo.
    confirmed = verify.tyre_off_vehicle(st, tyre_code=body.tire_id, plate=unit_id)

    try:
        rec = update(db, t("tires"), body.tire_id, {
            "unit_id": None,
            "is_mounted": 0,
            "axle_index": None,
            "wheel_index": None,
            "mount_position": None,
            "updated_at": now_ms(),
        })
        db.commit()
        audit(db, event, context, action="unbind", asset_type="tire", asset_id=body.tire_id,
              natural_key=tire.get("folio"), company_id=tire.get("company_id"),
              daijin_id=tire.get("daijin_id"),
              result=("success" if confirmed else "pending"), changes={"unit_id": None},
              error=(None if confirmed else "desmontaje de llanta no confirmado en la plataforma; el reconciliador lo reintentará"))
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (unbind tire local): {e}")

    if confirmed:
        return ok(rec)
    return pending({**rec, "sync_pending":
                    "la llanta aún se ve montada en la plataforma; queda pendiente de reintentar"})

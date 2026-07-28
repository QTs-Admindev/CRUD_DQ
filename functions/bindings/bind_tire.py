import json

from pydantic import BaseModel, ValidationError

from functions.bindings.bind_sensor import platform_bind_sensor
from shared.audit import audit
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
        audit(db, event, context, action="bind", asset_type="tire", asset_id=body.tire_id,
              natural_key=tire.get("folio"), company_id=tire.get("company_id"),
              daijin_id=tire.get("daijin_id"), result="success",
              changes={"unit_id": unit_id, "mount_position": body.mount_position})
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (bind tire local): {e}")

    # If the tire already has a sensor bound LOCALLY (never synced, because the
    # tire was unmounted at bind time), sync it to the platform now that the tire
    # is mounted. Best-effort: a platform failure leaves the sensor sync pending
    # (retried on a future bind or by the reconciler) but does NOT fail the mount.
    if tire.get("sensor_id"):
        sensor = get_by_id(db, t("sensors"), tire["sensor_id"])
        if sensor:
            try:
                platform_bind_sensor(
                    st,
                    tyre_code=body.tire_id,
                    axle=body.axle_index,
                    wheel=body.wheel_index,
                    sensor_code=sensor["sensorCode"],
                    vehicle_id=unit["daijin_id"],
                )
                audit(db, event, context, action="bind", asset_type="sensor",
                      asset_id=tire["sensor_id"], natural_key=sensor.get("sensorCode"),
                      company_id=sensor.get("company_id"), daijin_id=sensor.get("daijin_id"),
                      result="success", changes={"tire_id": body.tire_id, "synced_on_mount": True})
            except Exception as e:
                audit(db, event, context, action="bind", asset_type="sensor",
                      asset_id=tire["sensor_id"], natural_key=sensor.get("sensorCode"),
                      company_id=sensor.get("company_id"), daijin_id=sensor.get("daijin_id"),
                      result="pending", changes={"tire_id": body.tire_id},
                      error=f"deferred sensor sync on mount failed: {e}")

    return ok(rec)

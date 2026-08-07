import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.db.lock import with_asset_lock
from shared.smarttyre import verify
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending, SYNC_ERROR, sync_fail


class BindSensorRequest(BaseModel):
    sensor_id: int
    axle_index: int | None = None
    wheel_index: int | None = None


def platform_bind_sensor(st, *, tyre_code, axle, wheel, sensor_code, vehicle_id):
    """Bind a sensor to a tyre on the platform.

    Shared by the direct sensor bind (mounted+synced tire) and by the deferred
    sync that runs when a tire with a locally-bound sensor is later mounted.
    """
    st.post("/smartyre/openapi/tyre/sensor/bind", {
        "tyreCode": str(tyre_code),
        "axleIndex": axle,
        "wheelIndex": wheel,
        "sensorCode": sensor_code,
        "vehicleId": vehicle_id,
    })


@with_asset_lock(lambda e: "tire:" + str((e.get("pathParameters") or {}).get("id")))
def handler(event, context):
    # path: /tires/{id}/sensors/bind  -> id = llanta local
    try:
        tire_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")
    try:
        body = BindSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    tire = get_by_id(db, t("tires"), tire_id)
    if not tire:
        return error(404, "Llanta no encontrada")
    if tire.get("sensor_id"):
        return error(409, "La llanta ya tiene un sensor vinculado")
    sensor = get_by_id(db, t("sensors"), body.sensor_id)
    if not sensor:
        return error(404, "Sensor no encontrado")
    if not sensor.get("daijin_id"):
        return error(409, "El sensor aún no está listo")
    if sensor.get("company_id") != tire.get("company_id"):
        return error(409, "El sensor es de otra compañía; asígnalo a la compañía de la unidad primero")
    # Un solo dueño: si el sensor ya está en OTRA llanta viva, no lo robamos (guard previo;
    # el índice UNIQUE uq_tire_sensor_owner es el backstop atómico ante carreras).
    other = get_where(db, t("tires"),
                      "sensor_id = %s AND id <> %s AND (is_deleted IS NULL OR is_deleted = 0)",
                      [body.sensor_id, tire_id], 1)
    if other:
        return error(409, "Ese sensor ya está vinculado a otra llanta")

    axle = body.axle_index if body.axle_index is not None else tire.get("axle_index")
    wheel = body.wheel_index if body.wheel_index is not None else tire.get("wheel_index")

    # Is the tire mounted AND fully synced with the platform? Only then can we
    # bind the sensor remotely (the platform needs the vehicle). Otherwise we
    # bind LOCALLY now and defer the platform sync until the tire is mounted.
    unit = get_by_id(db, t("units"), tire["unit_id"]) if tire.get("unit_id") else None
    synced = bool(
        tire.get("unit_id")
        and tire.get("daijin_id")
        and unit
        and unit.get("daijin_id")
    )

    if synced:
        try:
            st = SmartTyreClient()
            platform_bind_sensor(
                st,
                tyre_code=tire_id,
                axle=axle,
                wheel=wheel,
                sensor_code=sensor["sensorCode"],
                vehicle_id=unit["daijin_id"],
            )
        except Exception:
            return error(502, SYNC_ERROR)

        # Confirmar por read-back que el sensor REALMENTE quedó asociado a la llanta en la
        # plataforma. Un 200 no basta: sin esto grabaríamos un falso éxito.
        confirmed = verify.sensor_on_tyre(st, sensor_code=sensor["sensorCode"], tyre_code=tire_id)

        # Local: la relación sensor<->llanta vive en tires.sensor_id.
        try:
            rec = update(db, t("tires"), tire_id, {
                "sensor_id": body.sensor_id,
                "axle_index": axle,
                "wheel_index": wheel,
                "updated_at": now_ms(),
            })
            db.commit()
            audit(db, event, context, action="bind", asset_type="sensor", asset_id=body.sensor_id,
                  natural_key=sensor.get("sensorCode"), company_id=sensor.get("company_id"),
                  daijin_id=sensor.get("daijin_id"),
                  result=("success" if confirmed else "pending"), changes={"tire_id": tire_id},
                  error=(None if confirmed else "bind de sensor no confirmado en la plataforma; el reconciliador lo reintentará"))
        except Exception as e:
            db.rollback()
            # Backstop atómico: uq_tire_sensor_owner rechaza el sensor si otra llanta lo
            # tomó en carrera. Revertimos el bind que ya hicimos en la plataforma y 409.
            if "Duplicate" in str(e) or "uq_tire_sensor_owner" in str(e):
                try:
                    st.post("/smartyre/openapi/tyre/sensor/unbind", {
                        "tyreCode": str(tire_id), "vehicleId": unit["daijin_id"],
                        "axleIndex": axle, "wheelIndex": wheel,
                        "sensorCode": sensor["sensorCode"]})
                except Exception:
                    pass
                return error(409, "Ese sensor ya está vinculado a otra llanta")
            return sync_fail(f"DB error (bind sensor local): {e}")
        if confirmed:
            return ok({**rec, "synced_to_platform": True})
        return pending({**rec, "synced_to_platform": False,
                        "sync_pending": "el sensor aún no se confirma en la plataforma; queda pendiente de reintentar"})

    # LOCAL-ONLY bind: the tire is unmounted (or the tire/unit is not yet synced).
    # We record the sensor<->tire relation locally and defer the platform bind;
    # it will run automatically when the tire is later mounted (see bind_tire).
    try:
        rec = update(db, t("tires"), tire_id, {
            "sensor_id": body.sensor_id,
            "axle_index": axle,
            "wheel_index": wheel,
            "updated_at": now_ms(),
        })
        db.commit()
        audit(db, event, context, action="bind", asset_type="sensor", asset_id=body.sensor_id,
              natural_key=sensor.get("sensorCode"), company_id=sensor.get("company_id"),
              daijin_id=sensor.get("daijin_id"), result="pending",
              changes={"tire_id": tire_id, "deferred": "platform sync until tire is mounted"})
        return ok({**rec, "synced_to_platform": False})
    except Exception as e:
        db.rollback()
        # Camino local puro (no se tocó la plataforma): solo traducir el choque a 409.
        if "Duplicate" in str(e) or "uq_tire_sensor_owner" in str(e):
            return error(409, "Ese sensor ya está vinculado a otra llanta")
        return sync_fail(f"DB error (bind sensor local): {e}")

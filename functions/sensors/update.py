import json
import logging

from pydantic import BaseModel, ValidationError

from shared.activation import NotOnPlatform, PlatformUnavailable, confirm_on_platform
from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import SYNC_ERROR, error, ok

_log = logging.getLogger(__name__)

VALID_STATUSES = {"registering", "active", "inactive"}

NOT_ON_PLATFORM_MSG = (
    "El sensor no está dado de alta en la plataforma; sincronízalo antes de activarlo"
)


class UpdateSensorRequest(BaseModel):
    status: str | None = None
    company_id: int | None = None


def handler(event, context):
    try:
        sensor_id = int(event["pathParameters"]["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de sensor inválido")

    try:
        body = UpdateSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    if body.status and body.status not in VALID_STATUSES:
        return error(422, f"status debe ser uno de: {', '.join(VALID_STATUSES)}")

    db = get_db()
    sensor = get_by_id(db, t("sensors"), sensor_id)
    # Una fila borrada es una línea cerrada: no se edita (mismo criterio que los
    # listados y el delete, que ya la tratan como inexistente).
    if not sensor or sensor.get("is_deleted"):
        return error(404, "Sensor no encontrado")

    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return ok(sensor)

    # 'active' se CONFIRMA, no se declara: el sensor tiene que estar dado de alta en la
    # plataforma. Si no, quedaría un activo local que la plataforma no conoce y que
    # además se sale de /sensors/resync y del cron de reconciliación.
    # Solo se confirma en la TRANSICIÓN a activo (o si la fila está activa sin id, que
    # es justo el estado corrupto que se quiere sanar). Repetir "activo" sobre una fila
    # ya activa y con id no consulta nada: así una edición de compañía no se cae cuando
    # la plataforma está lenta, y el invariante sigue en pie.
    needs_confirmation = (
        payload.get("status") == "active"
        and (sensor.get("status") != "active" or not sensor.get("daijin_id"))
    )
    healed_id = None
    if needs_confirmation:
        try:
            daijin_id = confirm_on_platform(sensor, "sensors")
        except NotOnPlatform:
            return error(409, NOT_ON_PLATFORM_MSG)
        except PlatformUnavailable as e:
            _log.warning("activación sin confirmar (sensor id=%s): %s", sensor_id, e)
            return error(502, SYNC_ERROR)
        if str(sensor.get("daijin_id") or "") != daijin_id:
            # Autocuración: la plataforma lo tiene con otro id (o nunca se guardó).
            payload["daijin_id"] = daijin_id
            healed_id = daijin_id

    payload["updated_at"] = now_ms()

    try:
        record = update(db, t("sensors"), sensor_id, payload)
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

    if healed_id:
        # Best-effort: el cambio ya está confirmado, un fallo de bitácora no lo tumba.
        try:
            audit(db, event, context, action="reconcile", asset_type="sensor",
                  asset_id=sensor_id, natural_key=sensor.get("sensorCode"),
                  company_id=record.get("company_id"), daijin_id=healed_id,
                  result="success", changes={"daijin_id": healed_id, "status": "active"})
        except Exception:
            pass

    return ok(record)

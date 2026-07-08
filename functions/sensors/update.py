import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok

VALID_STATUSES = {"registering", "active", "inactive"}


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
    if not sensor:
        return error(404, "Sensor no encontrado")

    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        return ok(sensor)
    payload["updated_at"] = now_ms()

    try:
        record = update(db, t("sensors"), sensor_id, payload)
        db.commit()
        return ok(record)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error: {e}")

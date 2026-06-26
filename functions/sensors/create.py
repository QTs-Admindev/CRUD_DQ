import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_field, get_by_id, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending
from shared.utils.validators import validate_hex12

# Default firmware version sent to Dajin (legacy default). Not stored locally.
SENSOR_VERSION = "404"


class CreateSensorRequest(BaseModel):
    # Sensors are registered into inventory WITHOUT a company; assigned later.
    sensor_code: str

    @field_validator("sensor_code")
    @classmethod
    def _check_sensor_code(cls, v: str) -> str:
        return validate_hex12(v, "sensor_code")


def handler(event, context):
    # 1. Validate input
    try:
        body = CreateSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()

    # 2. Local-first with idempotency: insert `registering` (or resume if it exists).
    try:
        existing = get_by_field(db, t("sensors"), "sensorCode", body.sensor_code)
        if existing and existing.get("daijin_id"):
            return ok(existing)  # already synced
        if existing:
            local_id = existing["id"]  # resume a half-finished registration
        else:
            try:
                rec = insert(db, t("sensors"), {
                    "sensorCode": body.sensor_code,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()  # durable before talking to Dajin
                local_id = rec["id"]
            except Exception:
                # Race / duplicate key: another process created it -> resume.
                db.rollback()
                existing = get_by_field(db, t("sensors"), "sensorCode", body.sensor_code)
                if not existing:
                    raise
                if existing.get("daijin_id"):
                    return ok(existing)
                local_id = existing["id"]
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (insert sensor): {e}")

    # 3. Sync with Dajin (idempotent). Natural key = sensorCode.
    try:
        st = SmartTyreClient()
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/sensor/list",
            list_filter={"sensorCode": body.sensor_code},
            insert_path="/smartyre/openapi/sensor/insert",
            insert_payload={"sensorCode": body.sensor_code, "version": SENSOR_VERSION},
        )
    except SmartTyreNotResolved:
        return pending(get_by_id(db, t("sensors"), local_id))
    except Exception as e:
        return pending({"id": local_id, "sensorCode": body.sensor_code, "reason": str(e)})

    # 4. Confirm the match and activate.
    try:
        rec = update(db, t("sensors"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate sensor, daijin_id={daijin_id}): {e}")

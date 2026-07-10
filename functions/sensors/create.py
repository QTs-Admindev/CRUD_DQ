import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_field, get_by_id, get_where, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending
from shared.utils.validators import validate_hex12

# Default firmware version sent to Dajin (legacy default). Not stored locally.
SENSOR_VERSION = "404"


class CreateSensorRequest(BaseModel):
    sensor_code: str
    # company_id opcional: si viene, el sensor nace asignado a esa compañía;
    # si es None, queda en inventario (sin compañía) como antes.
    company_id: int | None = None

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

    # 2. Local-first with idempotency: insert `registering` (or resume a LIVE one).
    #    A soft-deleted row (is_deleted=1) is NEVER reused nor matched. sensorCode is
    #    UNIQUE, so re-creating a deleted code is refused instead of reactivating it.
    live_sql = "sensorCode = %s AND (is_deleted IS NULL OR is_deleted = 0)"
    try:
        rows = get_where(db, t("sensors"), live_sql, [body.sensor_code], 1)
        existing = rows[0] if rows else None
        if existing and existing.get("daijin_id"):
            return ok(existing)  # already synced
        if existing:
            local_id = existing["id"]  # resume a half-finished (live) registration
        else:
            try:
                rec = insert(db, t("sensors"), {
                    "sensorCode": body.sensor_code,
                    "company_id": body.company_id,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()  # durable before talking to the platform
                local_id = rec["id"]
            except Exception:
                db.rollback()
                # UNIQUE(sensorCode): a soft-deleted row may hold this code. Don't reuse
                # that dead row (it stays deleted, as history), but free its code so the
                # sensor can be created anew, then insert a fresh row.
                dead = get_by_field(db, t("sensors"), "sensorCode", body.sensor_code)
                if dead and dead.get("is_deleted"):
                    update(db, t("sensors"), dead["id"], {
                        "sensorCode": f"{body.sensor_code}__del{dead['id']}",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    rec = insert(db, t("sensors"), {
                        "sensorCode": body.sensor_code,
                        "company_id": body.company_id,
                        "status": "registering",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    local_id = rec["id"]
                else:
                    # Race with a concurrent LIVE create -> resume it.
                    rows = get_where(db, t("sensors"), live_sql, [body.sensor_code], 1)
                    existing = rows[0] if rows else None
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
        audit(db, event, context, action="create", asset_type="sensor", asset_id=local_id,
              natural_key=body.sensor_code, company_id=body.company_id, result="pending")
        return pending(get_by_id(db, t("sensors"), local_id))
    except Exception as e:
        audit(db, event, context, action="create", asset_type="sensor", asset_id=local_id,
              natural_key=body.sensor_code, company_id=body.company_id, result="pending", error=str(e))
        return pending({"id": local_id, "sensorCode": body.sensor_code, "reason": str(e)})

    # 4. Confirm the match and activate.
    try:
        rec = update(db, t("sensors"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
        audit(db, event, context, action="create", asset_type="sensor", asset_id=local_id,
              natural_key=body.sensor_code, company_id=body.company_id,
              daijin_id=daijin_id, result="success")
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate sensor, daijin_id={daijin_id}): {e}")

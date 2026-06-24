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

# Versión por defecto de firmware con que el sistema viejo registra sensores.
SENSOR_VERSION = "404"


class CreateSensorRequest(BaseModel):
    sensor_code: str
    company_id: int

    @field_validator("sensor_code")
    @classmethod
    def _check_sensor_code(cls, v: str) -> str:
        return validate_hex12(v, "sensor_code")


def handler(event, context):
    # 1. Validar input
    try:
        body = CreateSensorRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()

    # 2. Local-first con idempotencia: insertar `registering` (o retomar si ya existe).
    try:
        existing = get_by_field(db, t("sensors"), "sensorCode", body.sensor_code)
        if existing and existing.get("daijin_id"):
            return ok(existing)  # ya sincronizado: nada que hacer
        if existing:
            local_id = existing["id"]  # quedó a medias antes; lo retomamos
        else:
            try:
                rec = insert(db, t("sensors"), {
                    "sensorCode": body.sensor_code,
                    "company_id": body.company_id,
                    "version": SENSOR_VERSION,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()  # durable antes de hablar con Dajin
                local_id = rec["id"]
            except Exception:
                # Carrera/clave duplicada: otro proceso lo creó en paralelo -> retomar.
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

    # 3. Sincronizar con Dajin (idempotente). Natural key = sensorCode.
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
        # Creado pero aún no resuelto -> queda registering; lo retoma la reconciliación.
        return pending(get_by_id(db, t("sensors"), local_id))
    except Exception as e:
        # Dajin caído/timeout -> queda registering; la reconciliación lo retoma.
        return pending({"id": local_id, "sensorCode": body.sensor_code, "reason": str(e)})

    # 4. Confirmar el match y activar.
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

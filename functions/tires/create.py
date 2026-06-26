import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_fields, get_by_id, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending

# Defaults que el sistema viejo manda a Dajin (no hay mapping tires_catalog -> Dajin).
TYRE_BRAND_ID = "1"
TYRE_SIZE_ID = "121"
TYRE_PATTERN = "FS591"


class CreateTireRequest(BaseModel):
    prefix: str
    folio: str
    company_id: int
    tires_catalog_id: int
    status: str = "new"  # status de negocio final (new/used/renewed/...)
    unit_id: int | None = None
    sensor_id: int | None = None
    current_depth: float = 0
    tire_mileage: float = 0
    is_mounted: int = 0
    mount_position: int | None = None
    axle_index: int | None = None
    wheel_index: int | None = None


def handler(event, context):
    # 1. Validar input
    try:
        body = CreateTireRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    # El folio debe ser único POR COMPAÑÍA (puede repetirse entre compañías distintas).
    key = {"folio": body.folio, "company_id": body.company_id}

    # 2. Local-first + idempotencia por (folio, company_id)
    try:
        existing = get_by_fields(db, t("tires"), key)
        if existing and existing.get("prefix") != body.prefix:
            return error(409, f"El folio '{body.folio}' ya está usado en esta compañía")
        if existing and existing.get("daijin_id"):
            return ok(existing)
        if existing:
            local_id = existing["id"]
        else:
            try:
                rec = insert(db, t("tires"), {
                    "prefix": body.prefix,
                    "folio": body.folio,
                    "company_id": body.company_id,
                    "tires_catalog_id": body.tires_catalog_id,
                    "unit_id": body.unit_id,
                    "sensor_id": body.sensor_id,
                    "current_depth": body.current_depth,
                    "tire_mileage": body.tire_mileage,
                    "is_mounted": body.is_mounted,
                    "mount_position": body.mount_position,
                    "axle_index": body.axle_index,
                    "wheel_index": body.wheel_index,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()
                local_id = rec["id"]
            except Exception:
                db.rollback()
                existing = get_by_fields(db, t("tires"), key)
                if not existing:
                    raise
                if existing.get("prefix") != body.prefix:
                    return error(409, f"El folio '{body.folio}' ya está usado en esta compañía")
                if existing.get("daijin_id"):
                    return ok(existing)
                local_id = existing["id"]
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (insert tire): {e}")

    # 3. Sync con Dajin. Natural key = id local (tyreCode) -> assume_new (no preexiste).
    try:
        st = SmartTyreClient()
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/tyre/list",
            list_filter={"tyreCode": str(local_id)},
            insert_path="/smartyre/openapi/tyre/insert",
            insert_payload={
                "tyreCode": str(local_id),
                "tyreBrandId": TYRE_BRAND_ID,
                "tyreSizeId": TYRE_SIZE_ID,
                "tyrePattern": TYRE_PATTERN,
                "initialTreadDepth": str(body.current_depth or 0),
                "totalDistance": body.tire_mileage or 0,
            },
            assume_new=True,
        )
    except SmartTyreNotResolved:
        return pending(get_by_id(db, t("tires"), local_id))
    except Exception as e:
        return pending({"id": local_id, "prefix": body.prefix, "folio": body.folio, "reason": str(e)})

    # 4. Activar con el status de negocio.
    try:
        rec = update(db, t("tires"), local_id, {
            "daijin_id": daijin_id,
            "status": body.status,
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate tire, daijin_id={daijin_id}): {e}")

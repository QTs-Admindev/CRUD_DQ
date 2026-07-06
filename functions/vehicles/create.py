import json

from pydantic import BaseModel, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_fields, get_by_id, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending


class CreateVehicleRequest(BaseModel):
    unit_identifier: str
    company_id: int
    unit_catalog_id: int
    tbox_id: int | None = None
    tbox_code: str | None = None
    vin: str = ""
    plates: str | None = None
    mileage: int = 0


def _dajin_type(catalog: dict) -> tuple[int, str]:
    """Calcula (isTractor, modelId) para Dajin a partir del unit_catalog (igual que el v1)."""
    name = (catalog.get("name") or "").lower()
    if catalog.get("type") == "trailer":
        return 2, "39"
    if "truck" in name:
        return 1, "40"
    return 0, "32"


def handler(event, context):
    # 1. Validar input
    try:
        body = CreateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    key = {
        "unit_identifier": body.unit_identifier,
        "company_id": body.company_id,
        "unit_catalog_id": body.unit_catalog_id,
    }

    # 2. Local-first + idempotencia por (unit_identifier, company_id, unit_catalog_id)
    try:
        existing = get_by_fields(db, t("units"), key)
        if existing and existing.get("daijin_id"):
            return ok(existing)
        if existing:
            local_id = existing["id"]
        else:
            try:
                rec = insert(db, t("units"), {
                    "unit_identifier": body.unit_identifier,
                    "company_id": body.company_id,
                    "unit_catalog_id": body.unit_catalog_id,
                    "tbox_id": body.tbox_id,
                    "vin": body.vin,
                    "plates": body.plates,
                    "mileage": body.mileage,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()
                local_id = rec["id"]
            except Exception:
                db.rollback()
                existing = get_by_fields(db, t("units"), key)
                if not existing:
                    raise
                if existing.get("daijin_id"):
                    return ok(existing)
                local_id = existing["id"]
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (insert unit): {e}")

    # 3. Lookup del catálogo (tabla de referencia REAL, sin prefijo test_)
    try:
        catalog = get_by_id(db, "unit_catalog", body.unit_catalog_id)
        if not catalog:
            return error(422, f"unit_catalog_id {body.unit_catalog_id} no existe")
    except Exception as e:
        return error(500, f"DB error (unit_catalog lookup): {e}")

    is_tractor, model_id = _dajin_type(catalog)

    # 4. Sync con Dajin. Natural key = id local (licensePlateNumber) -> assume_new.
    try:
        st = SmartTyreClient()
        payload = {
            "licensePlateNumber": str(local_id),
            "isTractor": is_tractor,
            "modelId": model_id,
            "axleTypeId": str(catalog.get("d_id") or ""),
            "orgId": str(body.company_id),
        }
        if body.tbox_code:
            payload["tboxCode"] = body.tbox_code
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/vehicle/list",
            list_filter={"licensePlateNumber": str(local_id)},
            insert_path="/smartyre/openapi/vehicle/insert",
            insert_payload=payload,
            assume_new=True,
        )
    except SmartTyreNotResolved:
        return pending(get_by_id(db, t("units"), local_id))
    except Exception as e:
        return pending({"id": local_id, "unit_identifier": body.unit_identifier, "reason": str(e)})

    # 5. Activar.
    try:
        rec = update(db, t("units"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate unit, daijin_id={daijin_id}): {e}")

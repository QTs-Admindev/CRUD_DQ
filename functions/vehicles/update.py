import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.smarttyre.client import SmartTyreClient
from shared.utils.clock import now_ms
from shared.utils.response import error, ok
from functions.vehicles.create import _dajin_type


class UpdateVehicleRequest(BaseModel):
    unit_identifier: str | None = None
    vin: str | None = None
    mileage: int | None = None
    unit_catalog_id: int | None = None
    company_id: int | None = None


def handler(event, context):
    # PUT /vehicles/{id} — edit a unit's local fields (unit_identifier/vin/mileage),
    # its catalog model (unit_catalog_id, synced to the platform first) and its
    # company (company_id, cascaded locally to its mounted tires/sensors/tbox).
    try:
        unit_id = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")
    try:
        body = UpdateVehicleRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Vehículo no encontrado")

    # Only the provided (not None) fields are persisted; the rest keep their value.
    changes: dict = {}
    if body.unit_identifier is not None:
        changes["unit_identifier"] = body.unit_identifier
    if body.vin is not None:
        changes["vin"] = body.vin
    if body.mileage is not None:
        changes["mileage"] = body.mileage

    # unit_catalog_id (model): validate the new catalog and, when the unit is already
    # synced, update the platform FIRST — the DB is never touched if that call fails.
    if body.unit_catalog_id is not None and body.unit_catalog_id != unit.get("unit_catalog_id"):
        try:
            new_catalog = get_by_id(db, "unit_catalog", body.unit_catalog_id)
        except Exception as e:
            return error(500, f"DB error (unit_catalog lookup): {e}")
        if not new_catalog:
            return error(422, f"unit_catalog_id {body.unit_catalog_id} no existe")

        if unit.get("daijin_id"):
            is_tractor, model_id = _dajin_type(new_catalog)
            tbox_code = ""
            if unit.get("tbox_id"):
                tbox = get_by_id(db, t("tboxes"), unit["tbox_id"])
                if tbox:
                    tbox_code = tbox.get("tboxCode") or ""
            try:
                st = SmartTyreClient()
                st.post("/smartyre/openapi/vehicle/update", {
                    "id": unit["daijin_id"],
                    "isTractor": is_tractor,
                    "licensePlateNumber": str(unit_id),
                    "axleTypeId": str(new_catalog.get("d_id") or ""),
                    "modelId": model_id,
                    "orgId": DAJIN_ORG_ID,
                    "tboxCode": tbox_code,
                })
            except Exception:
                return error(502, "No se pudo actualizar la unidad, intenta de nuevo")

        changes["unit_catalog_id"] = body.unit_catalog_id

    # company_id: change the unit and cascade the SAME company_id (local-only) to every
    # live mounted tire, each such tire's sensor, and the unit's tbox — one transaction.
    cascade_company_id = None
    if body.company_id is not None and body.company_id != unit.get("company_id"):
        changes["company_id"] = body.company_id
        cascade_company_id = body.company_id

    if not changes:
        return ok(unit)

    changes["updated_at"] = now_ms()

    try:
        if cascade_company_id is not None:
            mounted = get_where(
                db, t("tires"),
                "unit_id = %s AND (is_deleted IS NULL OR is_deleted = 0)",
                [unit_id],
            )
            for tire in mounted:
                update(db, t("tires"), tire["id"],
                       {"company_id": cascade_company_id, "updated_at": now_ms()})
                if tire.get("sensor_id"):
                    update(db, t("sensors"), tire["sensor_id"],
                           {"company_id": cascade_company_id, "updated_at": now_ms()})
            if unit.get("tbox_id"):
                update(db, t("tboxes"), unit["tbox_id"],
                       {"company_id": cascade_company_id, "updated_at": now_ms()})

        rec = update(db, t("units"), unit_id, changes)
        db.commit()
        audit(db, event, context, action="update", asset_type="unit", asset_id=unit_id,
              natural_key=rec.get("unit_identifier"), company_id=rec.get("company_id"),
              daijin_id=unit.get("daijin_id"), result="success", changes=changes)
        return ok(rec)
    except Exception as e:
        db.rollback()
        if "Duplicate" in str(e):
            return error(409, "Ya existe una unidad con ese identificador")
        return error(500, f"DB error (update unit): {e}")

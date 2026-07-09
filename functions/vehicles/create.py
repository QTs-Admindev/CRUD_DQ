import json

from pydantic import BaseModel, ValidationError

from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, insert, update
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
    """Compute (isTractor, modelId) for the platform from the unit_catalog (same as v1)."""
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
    # LIVE = not soft-deleted. Old rows may have is_deleted NULL (never deleted),
    # so "live" means NOT is_deleted=1 (NULL counts as live).
    live_sql = (
        "unit_identifier = %s AND company_id = %s AND unit_catalog_id = %s "
        "AND (is_deleted IS NULL OR is_deleted = 0)"
    )
    key_vals = [body.unit_identifier, body.company_id, body.unit_catalog_id]
    unit_fields = {
        "tbox_id": body.tbox_id,
        "vin": body.vin,
        "plates": body.plates,
        "mileage": body.mileage,
    }
    DUP_MSG = "Ya existe una unidad con ese identificador para esta compañía y tipo."

    def _live_unit():
        rows = get_where(db, t("units"), live_sql, key_vals, 1)
        return rows[0] if rows else None

    # 2. Local-first. Business rule: a soft-deleted row is NEVER reused nor matched.
    #    Duplicates are checked only against LIVE rows; a re-alta inserts a fresh row
    #    (a previously deleted unit with the same key is ignored entirely).
    try:
        existing = _live_unit()
        if existing:
            if existing.get("daijin_id"):
                return error(409, DUP_MSG)   # completed alta -> duplicate
            local_id = existing["id"]         # half-done (registering) -> resume
        else:
            try:
                rec = insert(db, t("units"), {
                    "unit_identifier": body.unit_identifier,
                    "company_id": body.company_id,
                    "unit_catalog_id": body.unit_catalog_id,
                    **unit_fields, "status": "registering", "updated_at": now_ms(),
                })
                db.commit()
                local_id = rec["id"]
            except Exception:
                db.rollback()
                # units has a UNIQUE on (unit_identifier, company_id, unit_catalog_id).
                # A soft-deleted row may hold it: don't reuse that dead row (it stays
                # deleted, as history), but free its key so the unit can be re-created,
                # then insert a fresh row.
                dead_sql = (
                    "unit_identifier = %s AND company_id = %s AND unit_catalog_id = %s "
                    "AND is_deleted = 1"
                )
                dead_rows = get_where(db, t("units"), dead_sql, key_vals, 1)
                dead = dead_rows[0] if dead_rows else None
                if dead:
                    update(db, t("units"), dead["id"], {
                        "unit_identifier": f"{body.unit_identifier}__del{dead['id']}",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    rec = insert(db, t("units"), {
                        "unit_identifier": body.unit_identifier,
                        "company_id": body.company_id,
                        "unit_catalog_id": body.unit_catalog_id,
                        **unit_fields, "status": "registering", "updated_at": now_ms(),
                    })
                    db.commit()
                    local_id = rec["id"]
                else:
                    # Race with a concurrent LIVE create of the SAME row.
                    existing = _live_unit()
                    if not existing:
                        raise
                    if existing.get("daijin_id"):
                        return error(409, DUP_MSG)
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
            "orgId": DAJIN_ORG_ID,  # Dajin siempre espera el org de Quinta (218), no el company_id
        }
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
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate unit, daijin_id={daijin_id}): {e}")

    # 6. Si viene tbox, atarlo en DAJIN (vehicle/update con tboxId = daijin del tbox),
    #    igual que el endpoint bind_tbox. El vínculo local (units.tbox_id) ya quedó en
    #    el insert; esto lo refleja en Dajin. Best-effort: si falla, la unidad ya está
    #    creada y el tbox se puede reasignar con /vehicles/{id}/tbox/bind.
    if body.tbox_id:
        try:
            tbox = get_by_id(db, t("tboxes"), body.tbox_id)
            if tbox and tbox.get("daijin_id"):
                st.post("/smartyre/openapi/vehicle/update", {
                    "id": daijin_id,
                    "isTractor": is_tractor,
                    "licensePlateNumber": str(local_id),
                    "axleTypeId": str(catalog.get("d_id") or ""),
                    "modelId": model_id,
                    "orgId": DAJIN_ORG_ID,  # Dajin siempre espera el org de Quinta (218), no el company_id
                    "tboxCode": tbox["tboxCode"],
                })
            else:
                return ok({**rec, "tbox_bind_warning": "el Qbox aún no está listo"})
        except Exception:
            return ok({**rec, "tbox_bind_warning": "no se pudo vincular el Qbox"})

    return ok(rec)

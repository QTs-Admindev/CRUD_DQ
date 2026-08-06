import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import DAJIN_ORG_ID, t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, insert, update
from shared.smarttyre import verify
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
    # tbox_id is deliberately NOT part of the initial insert: the Qbox link is only
    # recorded locally AFTER the platform confirms it (step 6), so MySQL never claims
    # a bind the platform doesn't have.
    unit_fields = {
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
    # resumed = picked up an existing (registering) row rather than a fresh insert. On the
    # resume path the platform vehicle may already be mid-insert by a racer, so step 4 does a
    # confirming GET-before-POST (assume_new=False) instead of blindly inserting a duplicate.
    resumed = False
    try:
        existing = _live_unit()
        if existing:
            if existing.get("daijin_id"):
                return error(409, DUP_MSG)   # completed alta -> duplicate
            local_id = existing["id"]         # half-done (registering) -> resume
            resumed = True
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
                # A soft-deleted row may still hold the UNIQUE key and block the
                # re-alta. Free EVERY dead row with this (identifier, company) — not
                # only the same type — so re-creating with ANY type works, regardless
                # of which columns the UNIQUE spans. Dead rows stay deleted (history);
                # they only release the name. (Same idea as the tire re-alta by
                # folio+company.)
                dead_sql = "unit_identifier = %s AND company_id = %s AND is_deleted = 1"
                dead_rows = get_where(
                    db, t("units"), dead_sql,
                    [body.unit_identifier, body.company_id], 1000,
                )
                if dead_rows:
                    for dead in dead_rows:
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
                    resumed = True
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

    # 4. Sync with the platform. Natural key = local id (licensePlateNumber) -> assume_new.
    try:
        st = SmartTyreClient()
        payload = {
            "licensePlateNumber": str(local_id),
            "isTractor": is_tractor,
            "modelId": model_id,
            "axleTypeId": str(catalog.get("d_id") or ""),
            "orgId": DAJIN_ORG_ID,  # the platform always expects Quinta's org (218), not the company_id
        }
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/vehicle/list",
            list_filter={"licensePlateNumber": str(local_id)},
            insert_path="/smartyre/openapi/vehicle/insert",
            insert_payload=payload,
            # Nuevo -> assume_new (licensePlateNumber = id local nuevo, no preexiste).
            # Resume -> GET-before-POST para no duplicar el vehículo upstream en carrera.
            assume_new=not resumed,
        )
    except SmartTyreNotResolved:
        audit(db, event, context, action="create", asset_type="unit", asset_id=local_id,
              natural_key=body.unit_identifier, company_id=body.company_id, result="pending")
        return pending(get_by_id(db, t("units"), local_id))
    except Exception as e:
        audit(db, event, context, action="create", asset_type="unit", asset_id=local_id,
              natural_key=body.unit_identifier, company_id=body.company_id,
              result="pending", error=str(e))
        return pending({"id": local_id, "unit_identifier": body.unit_identifier, "reason": str(e)})

    # 5. Activar.
    try:
        rec = update(db, t("units"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
        audit(db, event, context, action="create", asset_type="unit", asset_id=local_id,
              natural_key=body.unit_identifier, company_id=body.company_id,
              daijin_id=daijin_id, result="success")
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate unit, daijin_id={daijin_id}): {e}")

    # 6. Optional Qbox link (platform vehicle/update carrying the tbox). ALL-OR-NOTHING:
    #    units.tbox_id is written ONLY after the platform confirms the link, so MySQL
    #    never reports a Qbox bind the platform doesn't have. Before, tbox_id was set in
    #    the insert and a failed link still returned 200 — that was the divergence.
    if body.tbox_id:
        tbox = None
        try:
            tbox = get_by_id(db, t("tboxes"), body.tbox_id)
        except Exception:
            tbox = None

        if not (tbox and tbox.get("daijin_id")):
            # Qbox not synced with the platform yet -> can't link it there. Do NOT
            # record it locally; report pending so the FE doesn't show full success.
            audit(db, event, context, action="create", asset_type="unit", asset_id=local_id,
                  natural_key=body.unit_identifier, company_id=body.company_id,
                  daijin_id=daijin_id, result="pending",
                  error="qbox link deferred: qbox not synced with the platform")
            return pending({**rec,
                            "tbox_bind_pending": "el Qbox aún no está sincronizado con la plataforma; vincúlalo cuando lo esté"})

        try:
            st.post("/smartyre/openapi/vehicle/update", {
                "id": daijin_id,
                "isTractor": is_tractor,
                "licensePlateNumber": str(local_id),
                "axleTypeId": str(catalog.get("d_id") or ""),
                "modelId": model_id,
                "orgId": DAJIN_ORG_ID,  # the platform always expects Quinta's org (218), not the company_id
                "tboxCode": tbox["tboxCode"],
            })
        except Exception as e:
            # Platform link failed -> DO NOT record tbox_id locally (would diverge). Audit
            # the failure and report pending so it can be retried, not a fake success.
            audit(db, event, context, action="create", asset_type="unit", asset_id=local_id,
                  natural_key=body.unit_identifier, company_id=body.company_id,
                  daijin_id=daijin_id, result="pending", error=f"qbox link failed on the platform: {e}")
            return pending({**rec,
                            "tbox_bind_pending": "no se pudo vincular el Qbox en la plataforma; queda pendiente de reintentar"})

        # A 200 from vehicle/update does NOT prove the Qbox bound (it can be a phantom or a
        # no-op). Confirm by read-back before recording the link, so we never report a bind
        # the platform doesn't actually have. If unconfirmed we still record the intended
        # link locally (so the reconciler can find and complete it) but answer `pending`.
        confirmed = verify.tbox_bound(st, plate=local_id, tbox_code=tbox["tboxCode"])
        try:
            rec = update(db, t("units"), local_id, {"tbox_id": body.tbox_id, "updated_at": now_ms()})
            db.commit()
        except Exception as e:
            db.rollback()
            # The platform has the link but the local write failed -> report it (not a
            # silent success) so it gets reconciled/re-synced.
            return error(500, f"DB error (record qbox link, daijin_id={daijin_id}): {e}")

        if not confirmed:
            audit(db, event, context, action="bind", asset_type="tbox", asset_id=body.tbox_id,
                  natural_key=tbox.get("tboxCode"), company_id=body.company_id,
                  daijin_id=tbox.get("daijin_id"), result="pending",
                  error="qbox bind not confirmed on the platform; reconciler will retry")
            return pending({**rec,
                            "tbox_bind_pending": "el Qbox aún no se confirma montado en la plataforma; queda pendiente de reintentar"})

    return ok(rec)

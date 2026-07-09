import json

from pydantic import BaseModel, ValidationError, field_validator

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_field, get_by_id, get_where, insert, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import SmartTyreNotResolved, resolve_or_create
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending
from shared.utils.validators import validate_hex12


class CreateTboxRequest(BaseModel):
    tbox_code: str
    # company_id opcional: si viene, el tbox nace asignado a esa compañía;
    # si es None, queda en inventario (sin compañía) como antes.
    company_id: int | None = None

    @field_validator("tbox_code")
    @classmethod
    def _check_tbox_code(cls, v: str) -> str:
        return validate_hex12(v, "tbox_code")


def handler(event, context):
    # 1. Validate input
    try:
        body = CreateTboxRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()

    # 2. Local-first with idempotency: insert `registering` (or resume a LIVE one).
    #    A soft-deleted row (is_deleted=1) is NEVER reused nor matched. tboxCode is
    #    UNIQUE, so re-creating a deleted code is refused instead of reactivating it.
    live_sql = "tboxCode = %s AND (is_deleted IS NULL OR is_deleted = 0)"
    try:
        rows = get_where(db, t("tboxes"), live_sql, [body.tbox_code], 1)
        existing = rows[0] if rows else None
        if existing and existing.get("daijin_id"):
            return ok(existing)
        if existing:
            local_id = existing["id"]
        else:
            try:
                rec = insert(db, t("tboxes"), {
                    "tboxCode": body.tbox_code,
                    "company_id": body.company_id,
                    "status": "registering",
                    "updated_at": now_ms(),
                })
                db.commit()
                local_id = rec["id"]
            except Exception:
                db.rollback()
                # UNIQUE(tboxCode): a soft-deleted row may hold this code. Don't reuse
                # that dead row (it stays deleted, as history), but free its code so the
                # Qbox can be created anew, then insert a fresh row.
                dead = get_by_field(db, t("tboxes"), "tboxCode", body.tbox_code)
                if dead and dead.get("is_deleted"):
                    update(db, t("tboxes"), dead["id"], {
                        "tboxCode": f"{body.tbox_code}__del{dead['id']}",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    rec = insert(db, t("tboxes"), {
                        "tboxCode": body.tbox_code,
                        "company_id": body.company_id,
                        "status": "registering",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    local_id = rec["id"]
                else:
                    # Race with a concurrent LIVE create -> resume it.
                    rows = get_where(db, t("tboxes"), live_sql, [body.tbox_code], 1)
                    existing = rows[0] if rows else None
                    if not existing:
                        raise
                    if existing.get("daijin_id"):
                        return ok(existing)
                    local_id = existing["id"]
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (insert tbox): {e}")

    # 3. Sync with Dajin (idempotent). Natural key = tboxCode (external hardware code).
    try:
        st = SmartTyreClient()
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/tbox/list",
            list_filter={"tboxCode": body.tbox_code},
            insert_path="/smartyre/openapi/tbox/insert",
            insert_payload={"tboxCode": body.tbox_code},
        )
    except SmartTyreNotResolved:
        return pending(get_by_id(db, t("tboxes"), local_id))
    except Exception as e:
        return pending({"id": local_id, "tboxCode": body.tbox_code, "reason": str(e)})

    # 4. Confirm the match and activate.
    try:
        rec = update(db, t("tboxes"), local_id, {
            "daijin_id": daijin_id,
            "status": "active",
            "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (activate tbox, daijin_id={daijin_id}): {e}")

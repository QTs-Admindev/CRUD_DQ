"""Self-healing on create.

When a create resumes an existing local row that is already `active` with a
`daijin_id`, that id can be a PHANTOM: the row says it is synced, but the asset no
longer exists in Dajin (deleted upstream, or a half-finished sync). Returning it
as-is is how phantoms accumulate.

`heal_on_resume` verifies the stored `daijin_id` against Dajin by the asset's
natural key and, if it does not resolve, re-creates it — so every create leaves
BOTH systems consistent. It is best-effort: it NEVER raises, so a transient Dajin
problem can't break an otherwise-valid create (the row is returned unchanged and
the scheduled reconciliation sweep will retry).
"""
from shared.audit import audit
from shared.config import t
from shared.db.ops import update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import resolve_or_heal
from shared.utils.clock import now_ms
from shared.utils.response import ok


def heal_on_resume(db, event, context, *, existing, asset_type, table, natural_key,
                   list_path, list_filter, insert_path, insert_payload, assume_new=False):
    """Verify+heal an existing synced row against Dajin; return a Lambda `ok(...)`.

    asset_type/table: e.g. "tbox"/"tboxes". natural_key: the hardware code, for audit.
    list_*/insert_*: same args the create passes to resolve_or_create.
    """
    try:
        st = SmartTyreClient()
        real_id, changed = resolve_or_heal(
            st, stored_id=existing["daijin_id"],
            list_path=list_path, list_filter=list_filter,
            insert_path=insert_path, insert_payload=insert_payload, assume_new=assume_new,
        )
        if changed:
            rec = update(db, t(table), existing["id"], {
                "daijin_id": real_id, "status": "active", "updated_at": now_ms(),
            })
            db.commit()
            audit(db, event, context, action="reconcile", asset_type=asset_type,
                  asset_id=existing["id"], natural_key=natural_key,
                  company_id=existing.get("company_id"), daijin_id=real_id, result="success")
            return ok(rec)
    except Exception:
        # Best-effort: a Dajin/DB hiccup must not break the create. Leave the row as
        # it was; the reconciliation sweep will retry the heal later.
        try:
            db.rollback()
        except Exception:
            pass
    return ok(existing)

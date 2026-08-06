"""Self-healing on create.

When a create resumes an existing local row that is already `active` with a
`daijin_id`, that id can be a PHANTOM: the row says it is synced, but the asset no
longer exists in the platform (deleted upstream, or a half-finished sync). Returning it
as-is is how phantoms accumulate.

`heal_on_resume` verifies the stored `daijin_id` against the platform by the asset's
natural key and, if it does not resolve, re-creates it — so every create leaves
BOTH systems consistent. It is best-effort: it NEVER raises, so a transient platform
problem can't break an otherwise-valid create (the row is returned unchanged and
the scheduled reconciliation sweep will retry).
"""
import logging

from shared.audit import audit
from shared.config import t
from shared.db.ops import update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import resolve_or_heal
from shared.utils.clock import now_ms
from shared.utils.response import ok

_log = logging.getLogger(__name__)


def heal_on_resume(db, event, context, *, existing, asset_type, table, natural_key,
                   list_path, list_filter, insert_path, insert_payload):
    """Verify+heal an existing synced row against the platform; return a Lambda `ok(...)`.

    asset_type/table: e.g. "tbox"/"tboxes". natural_key: the hardware code, for audit.
    list_*/insert_*: same args the create passes to resolve_or_create.
    """
    try:
        st = SmartTyreClient()
        real_id, changed = resolve_or_heal(
            st, stored_id=existing["daijin_id"],
            list_path=list_path, list_filter=list_filter,
            insert_path=insert_path, insert_payload=insert_payload,
        )
        # Persist when the id moved, and also repair a half-finished status that
        # never reached 'active' (a resume of a stuck 'registering' row).
        needs_status_fix = existing.get("status") == "registering"
        if changed or needs_status_fix:
            fields = {"status": "active", "updated_at": now_ms()}
            if changed:
                fields["daijin_id"] = real_id
            rec = update(db, t(table), existing["id"], fields)
            db.commit()
            audit(db, event, context, action="reconcile", asset_type=asset_type,
                  asset_id=existing["id"], natural_key=natural_key,
                  company_id=existing.get("company_id"),
                  daijin_id=(real_id if changed else existing.get("daijin_id")),
                  result="success")
            return ok(rec)
    except Exception as e:
        # Best-effort: a the platform/DB hiccup must not break the create. But a heal may
        # have already POSTed a re-create upstream — record it (audit + log) so the
        # divergence is observable and the reconciliation sweep can retry.
        try:
            db.rollback()
        except Exception:
            pass
        _log.warning("heal_on_resume failed for %s id=%s: %s",
                     asset_type, existing.get("id"), e)
        try:
            audit(db, event, context, action="reconcile", asset_type=asset_type,
                  asset_id=existing.get("id"), natural_key=natural_key,
                  company_id=existing.get("company_id"), result="pending", error=str(e))
        except Exception:
            pass
    return ok(existing)

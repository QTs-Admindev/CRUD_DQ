"""Audit trail for asset operations (asset_audit_log).

Best-effort: writing the audit row must NEVER break the operation it records.
Call `audit(...)` AFTER the main work is committed — it runs its own insert+commit
on a clean transaction and swallows any error.

The `actor` (who did it) comes from the VERIFIED Cognito claims that API Gateway
puts in `event.requestContext.authorizer.claims`. It used to come from the
`X-Actor` header, which the client wrote itself: anyone could attribute any
action to anyone, which makes an audit trail worthless as evidence.

Falls back to "system" only for non-HTTP invocations (the reconciliation cron,
the bulk sync worker), which carry no requestContext at all.
"""
import json

from shared.auth import actor_from  # noqa: F401  (re-exported: callers import it from here)
from shared.db.ops import insert
from shared.utils.clock import now_ms

# asset_audit_log is a real (non-prefixed) log table shared across environments.
_TABLE = "asset_audit_log"


def audit(db, event=None, context=None, *, action, asset_type, actor=None,
          asset_id=None, natural_key=None, company_id=None, daijin_id=None,
          result="success", payload=None, changes=None, error=None):
    """Insert one audit row. Never raises; failures are swallowed.

    action:     create | update | bind | unbind | reconcile
    asset_type: unit | tire | sensor | tbox
    result:     success | pending | failed
    """
    try:
        if actor is None:
            actor = actor_from(event)
        request_id = getattr(context, "aws_request_id", None)
        insert(db, _TABLE, {
            "request_id": request_id,
            "actor": actor,
            "action": action,
            "asset_type": asset_type,
            "asset_id": asset_id,
            "natural_key": (str(natural_key) if natural_key is not None else None),
            "company_id": company_id,
            "daijin_id": (str(daijin_id) if daijin_id is not None else None),
            "result": result,
            "payload": json.dumps(payload, default=str) if payload is not None else None,
            "changes": json.dumps(changes, default=str) if changes is not None else None,
            "error": (error[:2000] if isinstance(error, str) else error),
            "created_at": now_ms(),
        })
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

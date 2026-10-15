"""Borra una unidad en segundo plano (invocación Event desde vehicles/delete). Sin
API Gateway de por medio no aplica el límite de 29 s.

delete_now es reanudable: lo ya desmontado o desvinculado se salta. Lo que siga
fallando deja deleting_at en NULL y el motivo en delete_error para reintentar.
"""
import json
import time

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error

from functions.vehicles.delete import delete_now

WAITS = (10, 30)
SAFETY_MS = 150_000


def _remaining_ms(context):
    return 900_000 if context is None else context.get_remaining_time_in_millis()


def _reason(resp):
    try:
        body = json.loads(resp.get("body") or "{}")
    except (TypeError, ValueError):
        body = {}
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"HTTP {resp.get('statusCode')}"


def handler(event, context):
    rid = int(event["unit_id"])
    actor = event.get("actor")

    db = get_db()
    rec = get_by_id(db, t("units"), rid)
    if not rec or rec.get("is_deleted") or not rec.get("deleting_at"):
        return {"skipped": True}

    ev = {"pathParameters": {"id": str(rid)},
          "headers": {"X-Actor": actor} if actor and actor != "system" else {}}

    resp = None
    for attempt in range(len(WAITS) + 1):
        try:
            resp = delete_now(ev, context)
        except Exception as e:
            db.rollback()
            resp = error(500, f"{type(e).__name__}: {e}")
        code = resp["statusCode"]
        if code in (200, 202):
            return {"deleted": True, "status": code}
        if 400 <= code < 500:
            break
        if attempt == len(WAITS) or _remaining_ms(context) < SAFETY_MS + WAITS[attempt] * 1000:
            break
        time.sleep(WAITS[attempt])

    try:
        update(db, t("units"), rid, {
            "deleting_at": None, "delete_error": _reason(resp)[:500], "updated_at": now_ms(),
        })
        db.commit()
    except Exception:
        db.rollback()
    return {"deleted": False, "status": resp["statusCode"]}

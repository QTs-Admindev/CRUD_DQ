"""Monta un paquete en segundo plano (invocación Event desde packages/assign o
vehicles/with-package). Sin API Gateway de por medio no aplica el límite de 29 s.

assign_now es reanudable: lo ya montado o ligado se salta, así que un reintento
continúa donde se quedó. Lo que siga fallando deja el paquete en 'assign_failed'
con el motivo, y el FE ofrece reintentar.
"""
import json
import time

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, update
from shared.utils.clock import now_ms
from shared.utils.response import error

from functions.packages.assign import assign_now, mark_failed

WAITS = (10, 30)
SAFETY_MS = 150_000


def _remaining_ms(context):
    return 900_000 if context is None else context.get_remaining_time_in_millis()


def _reason(resp):
    try:
        body = json.loads(resp.get("body") or "{}")
    except (TypeError, ValueError):
        return f"HTTP {resp.get('statusCode')}"
    if isinstance(body, dict):
        if body.get("error"):
            return body["error"] if isinstance(body["error"], str) else json.dumps(body["error"])
        data = body.get("data")
        if isinstance(data, dict) and data.get("reason"):
            reason = data["reason"]
            return reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False)
    return f"HTTP {resp.get('statusCode')}"


def handler(event, context):
    pid = int(event["package_id"])
    unit_id = int(event["unit_id"])
    actor = event.get("actor")

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg or pkg.get("status") != "assigning" or pkg.get("unit_id") != unit_id:
        return {"skipped": True, "status": (pkg or {}).get("status")}

    def progress(done, total):
        try:
            update(db, t("packages"), pid, {
                "assign_done": done, "assign_total": total, "updated_at": now_ms(),
            })
            db.commit()
        except Exception:
            db.rollback()

    ev = {"pathParameters": {"id": str(pid)},
          "body": json.dumps({"unit_id": unit_id}),
          "headers": {"X-Actor": actor} if actor and actor != "system" else {}}

    resp = None
    for attempt in range(len(WAITS) + 1):
        try:
            resp = assign_now(ev, context, on_progress=progress)
        except Exception as e:
            db.rollback()
            resp = error(500, f"{type(e).__name__}: {e}")
        code = resp["statusCode"]
        if code == 200:
            return {"assigned": True}
        if 400 <= code < 500:
            break
        if attempt == len(WAITS) or _remaining_ms(context) < SAFETY_MS + WAITS[attempt] * 1000:
            break
        time.sleep(WAITS[attempt])

    mark_failed(db, pid, _reason(resp))
    return {"assigned": False, "status": resp["statusCode"]}

"""Async worker that syncs a bulk-imported sensor batch against Dajin.

Invoked by bulk_create (Event invocation, no API Gateway → no 29 s cap; its own
timeout is 900 s). It ONLY touches the ids it receives — old `registering` rows
outside the batch are never picked up (this deliberately replaces the disabled
reconciliation cron for bulk imports).

Guarantees, without any cron:
  - Each id is resolved with the same idempotent `resolve_or_create` the single
    create uses (GET before POST → replays never duplicate in Dajin).
  - Running out of Lambda time → re-invokes itself with the remaining ids
    (same pass, no attempt burned).
  - Rows that failed (Dajin timeout/refusal) → re-invoked as pass+1, with a
    growing sleep between passes so a struggling Dajin gets breathing room,
    up to MAX_PASSES. Whatever still fails stays `registering`, visible in the
    FE, and re-importing the same file re-queues exactly those.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_in, update
from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import resolve_or_create
from shared.utils.clock import now_ms

# Firmware version Dajin expects on insert (same legacy default as create.py).
SENSOR_VERSION = "404"

# Parallel Dajin calls. The client is thread-safe (module-level httpx calls, the
# cached token is only read). Raise with care: Dajin is slow and rate-opaque.
CONCURRENCY = 4

# Full retry passes over the failing remainder before giving up.
MAX_PASSES = 8

# Re-invoke (instead of processing further) when less than this remains, so the
# in-flight chunk + the self-invoke always fit before the hard timeout.
SAFETY_MS = 90_000


def _remaining_ms(context) -> int:
    if context is None:
        return 900_000  # tests / local runs
    return context.get_remaining_time_in_millis()


def _reinvoke(ids: list[int], pass_num: int, actor: str) -> bool:
    fn = os.environ.get("BULK_SYNC_FUNCTION")
    if not fn or not ids:
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps({"ids": ids, "pass": pass_num, "actor": actor}).encode(),
        )
        return True
    except Exception:
        return False


def _sync_one(st, row):
    """Resolve one sensor against Dajin. Returns (id, daijin_id | None, error)."""
    try:
        daijin_id = resolve_or_create(
            st,
            list_path="/smartyre/openapi/sensor/list",
            list_filter={"sensorCode": row["sensorCode"]},
            insert_path="/smartyre/openapi/sensor/insert",
            insert_payload={"sensorCode": row["sensorCode"], "version": SENSOR_VERSION},
        )
        return row["id"], daijin_id, None
    except Exception as e:
        return row["id"], None, str(e)


def handler(event, context):
    ids = list(event.get("ids") or [])
    pass_num = int(event.get("pass") or 1)
    actor = event.get("actor") or "system"
    if not ids:
        return {"status": "ok", "resolved": 0, "pending": 0}

    db = get_db()

    # Only rows still worth syncing; anything already active/deleted drops out.
    rows = [
        r for r in get_in(db, t("sensors"), "id", ids)
        if r.get("status") == "registering"
        and not r.get("is_deleted")
        and not r.get("daijin_id")
    ]
    if not rows:
        return {"status": "ok", "resolved": 0, "pending": 0}

    try:
        st = SmartTyreClient()
    except Exception as e:
        # No auth, no sync possible this run. Burn a pass with backoff.
        if pass_num < MAX_PASSES:
            time.sleep(min(60, 10 * pass_num))
            _reinvoke([r["id"] for r in rows], pass_num + 1, actor)
        return {"status": "auth_failed", "error": str(e), "pending": len(rows)}

    resolved = 0
    failed_ids: list[int] = []
    leftover_ids: list[int] = []

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for i in range(0, len(rows), CONCURRENCY):
            if _remaining_ms(context) < SAFETY_MS:
                leftover_ids = [r["id"] for r in rows[i:]]
                break
            chunk = rows[i:i + CONCURRENCY]
            # Threads only talk to Dajin; every DB write happens here on the
            # main thread (the pymysql connection is not thread-safe).
            for rid, daijin_id, err in pool.map(lambda r: _sync_one(st, r), chunk):
                if daijin_id is None:
                    failed_ids.append(rid)
                    continue
                try:
                    rec = update(db, t("sensors"), rid, {
                        "daijin_id": daijin_id,
                        "status": "active",
                        "updated_at": now_ms(),
                    })
                    db.commit()
                    resolved += 1
                    audit(db, None, context, actor=actor, action="create",
                          asset_type="sensor", asset_id=rid,
                          natural_key=rec.get("sensorCode") if rec else None,
                          company_id=rec.get("company_id") if rec else None,
                          daijin_id=daijin_id, result="success")
                except Exception:
                    db.rollback()
                    failed_ids.append(rid)

    if leftover_ids:
        # Out of time mid-batch: continue in a fresh invocation, same pass.
        _reinvoke(failed_ids + leftover_ids, pass_num, actor)
        return {"status": "continued", "resolved": resolved,
                "pending": len(failed_ids) + len(leftover_ids), "pass": pass_num}

    if failed_ids and pass_num < MAX_PASSES:
        if resolved == 0:
            # Zero progress this pass → Dajin is struggling; back off before retrying.
            time.sleep(min(60, 10 * pass_num))
        _reinvoke(failed_ids, pass_num + 1, actor)
        return {"status": "retrying", "resolved": resolved,
                "pending": len(failed_ids), "pass": pass_num + 1}

    if failed_ids:
        # Exhausted MAX_PASSES: rows stay `registering` (visible in the FE);
        # re-importing the same file re-queues exactly these.
        audit(db, None, context, actor=actor, action="create", asset_type="sensor",
              natural_key=f"bulk:giveup:{len(failed_ids)}", result="failed",
              payload={"pending_ids": failed_ids, "passes": pass_num})

    return {"status": "ok", "resolved": resolved, "pending": len(failed_ids)}

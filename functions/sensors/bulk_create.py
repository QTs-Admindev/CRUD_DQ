"""Bulk sensor import: POST /sensors/bulk.

Local-first, same contract as the single create but for whole shipments (2000+
codes from an Excel file). This handler NEVER talks to Dajin — it only:

  1. Validates/normalizes the codes (hex-12) and drops in-file duplicates.
  2. Classifies against the local table:
       - live + daijin_id      -> already synced, nothing to do
       - live + registering    -> re-queued (guarantees an earlier stuck import
                                  gets finished by this run's worker)
       - soft-deleted holder   -> its code is freed (renamed) like the single
                                  create does; the code is then inserted fresh
       - unknown               -> inserted as `registering`
  3. Fires the async sync worker (Event invocation) with the queued ids and
     returns 202 immediately. The worker owns all Dajin traffic.

Everything is idempotent: re-posting the same file re-queues whatever is still
`registering` and skips the rest, so a failed/partial import is retried by
simply importing the same Excel again.
"""
import json
import os

import boto3
from pydantic import BaseModel, Field, ValidationError

from shared.audit import audit, actor_from
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_in, insert_many, update
from shared.utils.clock import now_ms
from shared.utils.response import error
from shared.utils.validators import HEX12

MAX_CODES = 5000


class BulkCreateRequest(BaseModel):
    # company_id optional: None leaves the whole batch in inventory (unassigned).
    company_id: int | None = None
    sensor_codes: list[str] = Field(min_length=1, max_length=MAX_CODES)


def _invoke_worker(ids: list[int], actor: str) -> bool:
    """Fire-and-forget invocation of the bulk sync worker. Failing to launch is
    NOT fatal: the rows stay `registering` and re-importing the file retries."""
    fn = os.environ.get("BULK_SYNC_FUNCTION")
    if not fn or not ids:
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps({"ids": ids, "pass": 1, "actor": actor}).encode(),
        )
        return True
    except Exception:
        return False


def handler(event, context):
    # 1. Validate input shape
    try:
        body = BulkCreateRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    # 2. Normalize + classify codes (invalid rows are reported, not fatal)
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    duplicates_in_file = 0
    for raw in body.sensor_codes:
        code = str(raw).strip().upper()
        if not HEX12.match(code):
            invalid.append(str(raw))
        elif code in seen:
            duplicates_in_file += 1
        else:
            seen.add(code)
            valid.append(code)
    if not valid:
        return error(422, {"invalid": invalid, "message": "sin códigos válidos"})

    db = get_db()

    # 3. Classify against the local table (live vs soft-deleted vs unknown)
    try:
        existing = get_in(db, t("sensors"), "sensorCode", valid)
        by_code = {r["sensorCode"]: r for r in existing}

        already_active: list[str] = []
        requeue_ids: list[int] = []
        dead_to_free: list[dict] = []
        new_codes: list[str] = []
        for code in valid:
            row = by_code.get(code)
            if row is None:
                new_codes.append(code)
            elif row.get("is_deleted"):
                # A dead row holds this UNIQUE code: free it (kept as history)
                # and register the code as brand new — same rule as create.py.
                dead_to_free.append(row)
                new_codes.append(code)
            elif row.get("daijin_id"):
                already_active.append(code)
            else:
                requeue_ids.append(row["id"])

        for dead in dead_to_free:
            update(db, t("sensors"), dead["id"], {
                "sensorCode": f"{dead['sensorCode']}__del{dead['id']}",
                "updated_at": now_ms(),
            })

        # 4. Insert the new ones as `registering` in one bulk statement
        ts = now_ms()
        insert_many(
            db, t("sensors"),
            ["sensorCode", "company_id", "status", "updated_at"],
            [(code, body.company_id, "registering", ts) for code in new_codes],
        )
        db.commit()  # durable before launching the worker

        # 5. Resolve the ids of everything this run must sync
        inserted_ids: list[int] = []
        if new_codes:
            inserted_ids = [
                r["id"]
                for r in get_in(db, t("sensors"), "sensorCode", new_codes, "id, sensorCode, is_deleted, daijin_id")
                if not r.get("is_deleted") and not r.get("daijin_id")
            ]
        queued_ids = requeue_ids + inserted_ids
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (bulk insert sensors): {e}")

    # 6. Launch the async worker that syncs the batch against Dajin
    actor = actor_from(event)
    worker_started = _invoke_worker(queued_ids, actor)

    audit(db, event, context, action="create", asset_type="sensor",
          natural_key=f"bulk:{len(valid)}", company_id=body.company_id,
          result="pending",
          payload={
              "received": len(body.sensor_codes),
              "inserted": len(inserted_ids),
              "requeued": len(requeue_ids),
              "already_active": len(already_active),
              "invalid": len(invalid),
              "duplicates_in_file": duplicates_in_file,
              "worker_started": worker_started,
          })

    return {
        "statusCode": 202,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "status": "registering",
            "message": "Lote encolado (sincronización en proceso)",
            "summary": {
                "received": len(body.sensor_codes),
                "queued": len(queued_ids),
                "inserted": len(inserted_ids),
                "requeued": len(requeue_ids),
                "already_active": len(already_active),
                "invalid": len(invalid),
                "duplicates_in_file": duplicates_in_file,
            },
            "already_active_codes": already_active,
            "invalid_codes": invalid,
            "ids": queued_ids,
            "worker_started": worker_started,
        }, default=str),
    }

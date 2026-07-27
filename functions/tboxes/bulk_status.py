"""Batch status for a Qbox bulk import: POST /tboxes/bulk/status.

Powers the FE progress dock. Given the ids returned by POST /tboxes/bulk, it
reports how many are already synced (`active`) vs still `registering`, so the
front-end can fill a progress bar and notify when the batch is done — without
holding the upload modal open (the sync runs in the background worker).

Read-only, never touches the platform. Body: { "ids": [int, ...] }.
"""
import json

from pydantic import BaseModel, Field, ValidationError

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_in
from shared.utils.response import error, ok

MAX_IDS = 5000


class StatusRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_IDS)


def handler(event, context):
    try:
        body = StatusRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    try:
        rows = get_in(db, t("tboxes"), "id", body.ids, "id, status, is_deleted, daijin_id")
    except Exception as e:
        return error(500, f"DB error (bulk status tboxes): {e}")

    total = len(body.ids)
    active = 0        # synced with the platform -> done
    registering = 0   # still being synced by the worker
    for r in rows:
        if r.get("is_deleted"):
            continue
        if r.get("status") == "active" or r.get("daijin_id"):
            active += 1
        elif r.get("status") == "registering":
            registering += 1

    # Rows the ids point to but that no longer exist / were deleted are counted
    # as "gone" so the bar can still complete instead of hanging forever.
    found = active + registering
    gone = max(0, total - found)

    return ok({
        "total": total,
        "done": active,
        "pending": registering,
        "gone": gone,
        # Batch is settled when nothing is left registering (all synced, or the
        # worker exhausted its retries and the rest stays registering — the FE
        # decides to stop polling on a stable count / timeout).
        "finished": registering == 0,
    })

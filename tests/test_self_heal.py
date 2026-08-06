"""Tests for the self-heal-on-create feature.

Covers:
- shared/smarttyre/sync.py :: resolve_or_heal — verify a stored daijin_id against
  Dajin by natural key, retry a transient empty read across the backoff, and only
  re-create (GET-before-POST, never assume_new) when it is truly a phantom.
- shared/reconcile.py :: heal_on_resume — best-effort DB heal; also repairs a
  stuck 'registering' status; never propagates (writes a 'pending' audit on error).
"""
import json

from shared import reconcile
from shared.smarttyre import sync
from shared.smarttyre.sync import resolve_or_heal


class FakeST:
    """Fake SmartTyre client. `existing` = what GET returns before creating;
    `after` = what GET returns after the POST (see tests/test_sync.py)."""

    def __init__(self, existing=None, after=None, fail=False):
        self._existing = existing or []
        self._after = after or []
        self.fail = fail
        self.created = False
        self.posts = []

    def get(self, path, params):
        if self.fail:
            raise ConnectionError("Dajin down")
        return {"records": self._after if self.created else self._existing}

    def post(self, path, body):
        if self.fail:
            raise ConnectionError("Dajin down")
        self.posts.append((path, body))
        self.created = True
        return "Success"


class SeqST:
    """Returns a scripted sequence of GET record-lists, one per `_find_id` call.
    After the sequence is exhausted the last entry keeps being returned."""

    def __init__(self, get_records):
        self._seq = list(get_records)
        self._i = 0
        self.posts = []

    def get(self, path, params):
        recs = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return {"records": recs}

    def post(self, path, body):
        self.posts.append((path, body))
        return "Success"


def _heal(st, stored_id, backoff=()):
    return resolve_or_heal(
        st,
        stored_id=stored_id,
        list_path="/list",
        list_filter={"sensorCode": "X"},
        insert_path="/insert",
        insert_payload={"sensorCode": "X"},
        backoff=backoff,
    )


# ---------------------------------------------------------------------------
# resolve_or_heal
# ---------------------------------------------------------------------------

def test_heal_stored_id_still_valid_and_equal():
    # Natural key resolves to the SAME id we already stored -> keep it, no create.
    st = FakeST(existing=[{"id": "77"}])
    found, changed = _heal(st, stored_id="77")
    assert str(found) == "77"
    assert changed is False
    assert st.posts == []  # nothing re-created


def test_heal_natural_key_resolves_to_different_id():
    # The asset exists in Dajin but under a DIFFERENT id than we stored.
    # Return the real id, flag changed, and do NOT create a duplicate.
    st = FakeST(existing=[{"id": 99}])
    found, changed = _heal(st, stored_id="77")
    assert str(found) == "99"
    assert changed is True
    assert st.posts == []


def test_heal_phantom_triggers_recreate():
    # Phantom: the stored id never resolves in Dajin (empty across all retries).
    # Re-create and return the new id, flagged as changed.
    st = FakeST(existing=[], after=[{"id": 500}])
    found, changed = _heal(st, stored_id="77")
    assert str(found) == "500"
    assert changed is True
    assert len(st.posts) == 1  # re-created upstream


def test_heal_phantom_that_recreates_to_same_id_is_not_changed():
    # Edge: phantom re-create happens to yield the same id -> not "changed".
    st = FakeST(existing=[], after=[{"id": "77"}])
    found, changed = _heal(st, stored_id="77")
    assert str(found) == "77"
    assert changed is False
    assert len(st.posts) == 1


# --- Regression: a single transient empty read must NOT trigger a re-create ---

def test_heal_transient_empty_read_retries_and_does_not_recreate(monkeypatch):
    # BLOCKER regression: first `_find_id` reads empty (transient), the retry
    # finds the asset. It must resolve to that id with ZERO inserts (no duplicate).
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    st = SeqST([[], [{"id": 99}]])  # empty, then resolves
    found, changed = _heal(st, stored_id="77", backoff=(0.3,))
    assert str(found) == "99"
    assert changed is True          # id moved 77 -> 99
    assert st.posts == []           # NO re-create despite the first empty read


def test_heal_transient_empty_then_same_id_is_not_changed(monkeypatch):
    # Same retry path, but the retry confirms the stored id -> not changed, no insert.
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    st = SeqST([[], [{"id": "77"}]])
    found, changed = _heal(st, stored_id="77", backoff=(0.3,))
    assert str(found) == "77"
    assert changed is False
    assert st.posts == []


def test_heal_phantom_after_all_retries_recreates_with_confirming_get(monkeypatch):
    # Empty across every retry -> exactly one re-create via resolve_or_create,
    # and it must confirm (assume_new=False), never assume-new.
    monkeypatch.setattr(sync.time, "sleep", lambda *_: None)
    calls = []

    def fake_resolve_or_create(st, **kwargs):
        calls.append(kwargs)
        return "500"

    monkeypatch.setattr(sync, "resolve_or_create", fake_resolve_or_create)
    st = SeqST([[]])  # always empty
    found, changed = _heal(st, stored_id="77", backoff=(0.3, 0.8))

    assert str(found) == "500"
    assert changed is True
    assert len(calls) == 1                     # exactly one re-create
    assert calls[0]["assume_new"] is False     # confirming GET-before-POST


# ---------------------------------------------------------------------------
# heal_on_resume
# ---------------------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _wire(monkeypatch, *, resolve_result=None, resolve_raises=None):
    """Patch heal_on_resume's collaborators. Returns (updates, audits, db)."""
    updates = []
    audits = []
    db = FakeDB()

    monkeypatch.setattr(reconcile, "SmartTyreClient", lambda: object())

    def fake_resolve(st, **kwargs):
        if resolve_raises is not None:
            raise resolve_raises
        return resolve_result

    monkeypatch.setattr(reconcile, "resolve_or_heal", fake_resolve)
    monkeypatch.setattr(
        reconcile, "update",
        lambda db, table, rid, data: (updates.append((table, rid, data)),
                                      {"id": rid, **data})[1],
    )
    monkeypatch.setattr(
        reconcile, "audit",
        lambda *a, **k: audits.append(k),
    )
    return updates, audits, db


def _call_heal(db, existing):
    return reconcile.heal_on_resume(
        db, {}, None,
        existing=existing, asset_type="sensor", table="sensors",
        natural_key="AABBCCDDEEFF",
        list_path="/smartyre/openapi/sensor/list",
        list_filter={"sensorCode": "AABBCCDDEEFF"},
        insert_path="/smartyre/openapi/sensor/insert",
        insert_payload={"sensorCode": "AABBCCDDEEFF"},
    )


def _body(resp):
    return json.loads(resp["body"])


def test_heal_on_resume_changed_updates_and_audits(monkeypatch):
    updates, audits, db = _wire(monkeypatch, resolve_result=("900", True))
    existing = {"id": 5, "daijin_id": "77", "company_id": 100, "status": "active"}

    resp = _call_heal(db, existing)

    assert resp["statusCode"] == 200
    # Row was healed: new daijin_id + reactivated.
    assert len(updates) == 1
    table, rid, data = updates[0]
    assert (table, rid) == ("sensors", 5)
    assert data["daijin_id"] == "900"
    assert data["status"] == "active"
    assert db.commits == 1
    # One reconcile audit row was written.
    assert len(audits) == 1
    assert audits[0]["action"] == "reconcile"
    assert audits[0]["result"] == "success"
    assert audits[0]["daijin_id"] == "900"
    assert _body(resp)["daijin_id"] == "900"


def test_heal_on_resume_not_changed_active_returns_existing_no_update(monkeypatch):
    # id unchanged AND already 'active' -> nothing to do.
    updates, audits, db = _wire(monkeypatch, resolve_result=("77", False))
    existing = {"id": 5, "daijin_id": "77", "company_id": 100, "status": "active"}

    resp = _call_heal(db, existing)

    assert resp["statusCode"] == 200
    assert updates == []          # nothing written
    assert audits == []
    assert db.commits == 0
    assert _body(resp)["daijin_id"] == "77"


def test_heal_on_resume_not_changed_but_registering_repairs_status(monkeypatch):
    # id unchanged, but the row is a stuck 'registering' -> repair status to 'active',
    # commit, and write a success reconcile audit (keeping the same daijin_id).
    updates, audits, db = _wire(monkeypatch, resolve_result=("77", False))
    existing = {"id": 5, "daijin_id": "77", "company_id": 100, "status": "registering"}

    resp = _call_heal(db, existing)

    assert resp["statusCode"] == 200
    assert len(updates) == 1
    table, rid, data = updates[0]
    assert (table, rid) == ("sensors", 5)
    assert data["status"] == "active"
    assert "daijin_id" not in data          # id did not move, so it is not rewritten
    assert db.commits == 1
    assert len(audits) == 1
    assert audits[0]["action"] == "reconcile"
    assert audits[0]["result"] == "success"
    assert audits[0]["daijin_id"] == "77"   # unchanged id is still recorded
    assert _body(resp)["status"] == "active"


def test_heal_on_resume_swallows_errors_and_rolls_back(monkeypatch):
    updates, audits, db = _wire(monkeypatch, resolve_raises=ConnectionError("Dajin down"))
    existing = {"id": 5, "daijin_id": "77", "company_id": 100, "status": "active"}

    # Must NOT raise.
    resp = _call_heal(db, existing)

    assert resp["statusCode"] == 200
    assert _body(resp)["daijin_id"] == "77"   # returned unchanged
    assert updates == []
    assert db.rollbacks == 1                  # best-effort rollback happened
    # The failure path is no longer silent: a 'pending' audit row is attempted.
    assert len(audits) == 1
    assert audits[0]["result"] == "pending"
    assert "Dajin down" in audits[0]["error"]


def test_heal_on_resume_db_update_failure_is_swallowed(monkeypatch):
    # resolve says "changed", but the DB update blows up -> still no raise,
    # row returned as-is, rollback attempted, and a 'pending' audit is written.
    updates, audits, db = _wire(monkeypatch, resolve_result=("900", True))

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(reconcile, "update", boom)
    existing = {"id": 5, "daijin_id": "77", "company_id": 100, "status": "active"}

    resp = _call_heal(db, existing)

    assert resp["statusCode"] == 200
    assert _body(resp)["daijin_id"] == "77"
    assert db.rollbacks == 1
    assert len(audits) == 1
    assert audits[0]["result"] == "pending"
    assert "db down" in audits[0]["error"]

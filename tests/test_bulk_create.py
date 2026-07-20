import json

import pytest

from functions.sensors import bulk_create as mod


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeStore:
    """In-memory table mimicking shared.db.ops for the bulk helpers."""

    def __init__(self, rows=None):
        self.rows = {r["id"]: dict(r) for r in (rows or [])}
        self.seq = max(self.rows, default=0)
        self.inserted = []

    def get_in(self, db, table, field, values, columns="*"):
        vals = set(values)
        return [dict(r) for r in self.rows.values() if r.get(field) in vals]

    def insert_many(self, db, table, columns, rows):
        for row in rows:
            self.seq += 1
            rec = {"id": self.seq, **dict(zip(columns, row))}
            self.rows[self.seq] = rec
            self.inserted.append(rec)
        return len(rows)

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])


@pytest.fixture
def wire(monkeypatch):
    def setup(rows=None, worker_ok=True):
        store = FakeStore(rows)
        db = FakeDB()
        invocations = []

        def fake_invoke(ids, actor):
            invocations.append({"ids": list(ids), "actor": actor})
            return worker_ok

        monkeypatch.setattr(mod, "get_db", lambda: db)
        monkeypatch.setattr(mod, "get_in", store.get_in)
        monkeypatch.setattr(mod, "insert_many", store.insert_many)
        monkeypatch.setattr(mod, "update", store.update)
        monkeypatch.setattr(mod, "audit", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_invoke_worker", fake_invoke)
        return store, db, invocations

    return setup


def _event(codes, company=100):
    return {
        "body": json.dumps({"sensor_codes": codes, "company_id": company}),
        "headers": {"X-Actor": "cesar@quinta.tech"},
    }


def _body(resp):
    return json.loads(resp["body"])


def test_inserts_new_codes_and_launches_worker(wire):
    store, db, invocations = wire()

    resp = mod.handler(_event(["a4c13873c3e6", "A4C13873C3E7"]), None)

    assert resp["statusCode"] == 202
    body = _body(resp)
    assert body["summary"]["inserted"] == 2
    assert body["summary"]["queued"] == 2
    # normalized to uppercase before storing
    assert {r["sensorCode"] for r in store.inserted} == {"A4C13873C3E6", "A4C13873C3E7"}
    assert all(r["status"] == "registering" for r in store.inserted)
    assert db.commits >= 1
    assert invocations and invocations[0]["ids"] == body["ids"]
    assert invocations[0]["actor"] == "cesar@quinta.tech"


def test_classifies_invalid_duplicates_and_active(wire):
    store, db, invocations = wire(rows=[
        {"id": 1, "sensorCode": "AAAAAAAAAAA1", "daijin_id": 99,
         "status": "active", "is_deleted": 0},
    ])

    resp = mod.handler(_event([
        "AAAAAAAAAAA1",   # already synced -> skipped
        "BBBBBBBBBBB2",   # new
        "bbbbbbbbbbb2",   # duplicate in file (same code)
        "not-hex",        # invalid
    ]), None)

    body = _body(resp)
    assert body["summary"] == {
        "received": 4, "queued": 1, "inserted": 1, "requeued": 0,
        "already_active": 1, "invalid": 1, "duplicates_in_file": 1,
    }
    assert body["already_active_codes"] == ["AAAAAAAAAAA1"]
    assert body["invalid_codes"] == ["not-hex"]


def test_requeues_stuck_registering_rows(wire):
    # A previous half-finished import left the row in `registering`:
    # re-importing the same file must queue it again, not duplicate it.
    store, db, invocations = wire(rows=[
        {"id": 7, "sensorCode": "CCCCCCCCCCC3", "daijin_id": None,
         "status": "registering", "is_deleted": 0},
    ])

    resp = mod.handler(_event(["CCCCCCCCCCC3"]), None)

    body = _body(resp)
    assert body["summary"]["requeued"] == 1
    assert body["summary"]["inserted"] == 0
    assert body["ids"] == [7]
    assert store.inserted == []  # no duplicate row


def test_frees_soft_deleted_code_and_reinserts(wire):
    store, db, invocations = wire(rows=[
        {"id": 3, "sensorCode": "DDDDDDDDDDD4", "daijin_id": "55",
         "status": "active", "is_deleted": 1},
    ])

    resp = mod.handler(_event(["DDDDDDDDDDD4"]), None)

    body = _body(resp)
    assert body["summary"]["inserted"] == 1
    # dead row keeps history under a renamed code
    assert store.rows[3]["sensorCode"] == "DDDDDDDDDDD4__del3"
    # fresh live row owns the original code
    assert store.inserted[0]["sensorCode"] == "DDDDDDDDDDD4"


def test_all_invalid_returns_422(wire):
    wire()
    resp = mod.handler(_event(["nope", "123"]), None)
    assert resp["statusCode"] == 422


def test_worker_launch_failure_is_not_fatal(wire):
    store, db, invocations = wire(worker_ok=False)

    resp = mod.handler(_event(["EEEEEEEEEEE5"]), None)

    assert resp["statusCode"] == 202  # rows are safe locally; re-import retries
    assert _body(resp)["worker_started"] is False

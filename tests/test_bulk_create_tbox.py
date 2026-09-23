"""Carga masiva de Qbox: el lote (batch_code). Espejo de test_bulk_create.py."""
import json

import pytest

from functions.tboxes import bulk_create as mod


class FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeStore:
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
    def setup(rows=None):
        store = FakeStore(rows)
        monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
        monkeypatch.setattr(mod, "get_in", store.get_in)
        monkeypatch.setattr(mod, "insert_many", store.insert_many)
        monkeypatch.setattr(mod, "update", store.update)
        monkeypatch.setattr(mod, "audit", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_invoke_worker", lambda ids, actor: True)
        return store

    return setup


def _event(codes, batch_code=None, company=100):
    body = {"tbox_codes": codes, "company_id": company}
    if batch_code is not None:
        body["batch_code"] = batch_code
    return {"body": json.dumps(body), "headers": {}}


def _body(resp):
    return json.loads(resp["body"])


def test_new_tboxes_are_born_with_the_batch(wire):
    store = wire()

    resp = mod.handler(_event(["10B41D30EA79", "10B41D30EA7A"], "  LOTE-Q-7 "), None)

    assert resp["statusCode"] == 202
    assert _body(resp)["batch_code"] == "LOTE-Q-7"
    assert {r["batch_code"] for r in store.inserted} == {"LOTE-Q-7"}


def test_requeued_without_batch_takes_it_and_with_batch_keeps_it(wire):
    store = wire(rows=[
        {"id": 7, "tboxCode": "10B41D30EA79", "daijin_id": None, "status": "registering",
         "is_deleted": 0, "batch_code": None},
        {"id": 8, "tboxCode": "10B41D30EA7A", "daijin_id": None, "status": "registering",
         "is_deleted": 0, "batch_code": "LOTE-VIEJO"},
    ])

    mod.handler(_event(["10B41D30EA79", "10B41D30EA7A"], "LOTE-NUEVO"), None)

    assert store.rows[7]["batch_code"] == "LOTE-NUEVO"
    assert store.rows[8]["batch_code"] == "LOTE-VIEJO"


def test_already_active_tbox_keeps_no_batch(wire):
    store = wire(rows=[
        {"id": 1, "tboxCode": "10B41D30EA79", "daijin_id": 9, "status": "active",
         "is_deleted": 0, "batch_code": None},
    ])

    mod.handler(_event(["10B41D30EA79"], "LOTE-NUEVO"), None)

    assert store.rows[1]["batch_code"] is None


def test_without_batch_works_as_before(wire):
    store = wire()

    resp = mod.handler(_event(["10B41D30EA7B"]), None)

    assert resp["statusCode"] == 202
    assert store.inserted[0]["batch_code"] is None


def test_invalid_batch_is_422(wire):
    wire()
    assert mod.handler(_event(["10B41D30EA7C"], "X" * 65), None)["statusCode"] == 422

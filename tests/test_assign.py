import json

from functions.sensors import assign as sassign
from functions.tboxes import assign as tassign


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self, rows, bound=False):
        self.rows = rows
        self.bound = bound

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def exists(self, db, table, filters):
        return self.bound


def _wire(monkeypatch, mod, store):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "exists", store.exists)


def _ev(rid, company):
    return {"pathParameters": {"id": str(rid)}, "body": json.dumps({"company_id": company})}


def test_assign_sensor_happy(monkeypatch):
    store = FakeStore({1: {"id": 1, "company_id": None}})
    _wire(monkeypatch, sassign, store)
    resp = sassign.handler(_ev(1, 100), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["company_id"] == 100


def test_assign_sensor_blocked_when_bound(monkeypatch):
    store = FakeStore({1: {"id": 1, "company_id": None}}, bound=True)
    _wire(monkeypatch, sassign, store)
    resp = sassign.handler(_ev(1, 100), None)
    assert resp["statusCode"] == 409
    assert store.rows[1]["company_id"] is None


def test_assign_tbox_happy(monkeypatch):
    store = FakeStore({1: {"id": 1, "company_id": None}})
    _wire(monkeypatch, tassign, store)
    resp = tassign.handler(_ev(1, 100), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["company_id"] == 100


def test_assign_requires_company_id_422(monkeypatch):
    store = FakeStore({1: {"id": 1, "company_id": None}})
    _wire(monkeypatch, sassign, store)
    resp = sassign.handler({"pathParameters": {"id": "1"}, "body": "{}"}, None)
    assert resp["statusCode"] == 422

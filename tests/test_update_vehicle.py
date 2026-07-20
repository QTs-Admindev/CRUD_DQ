import json

from functions.vehicles import update as mod


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self):
        self.rows = {1: {"id": 1, "unit_identifier": "OLD", "company_id": 100}}

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None


def _wire(monkeypatch, store):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)


def _ev(unit_id, body):
    return {"pathParameters": {"id": str(unit_id)}, "body": json.dumps(body)}


def test_update_unit_identifier_happy(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {"unit_identifier": "NEW"}), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["unit_identifier"] == "NEW"


def test_not_found_404(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(999, {"unit_identifier": "X"}), None)
    assert resp["statusCode"] == 404


def test_missing_field_422(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {}), None)
    assert resp["statusCode"] == 422

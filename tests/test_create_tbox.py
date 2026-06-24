import json

import pytest

from functions.tboxes import create as mod


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.seq = 0

    def insert(self, db, table, data):
        self.seq += 1
        self.rows[self.seq] = {"id": self.seq, **data}
        return dict(self.rows[self.seq])

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def get_by_field(self, db, table, field, value):
        for r in self.rows.values():
            if r.get(field) == value:
                return dict(r)
        return None


class FakeSmartTyre:
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


@pytest.fixture
def wire(monkeypatch):
    store = FakeStore()
    db = FakeDB()

    def setup(st):
        monkeypatch.setattr(mod, "get_db", lambda: db)
        monkeypatch.setattr(mod, "insert", store.insert)
        monkeypatch.setattr(mod, "update", store.update)
        monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
        monkeypatch.setattr(mod, "get_by_field", store.get_by_field)
        monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)
        return store, db

    return setup


def _event(code="10B41D30EA79", company=100):
    return {"body": json.dumps({"tbox_code": code, "company_id": company})}


def _body(resp):
    return json.loads(resp["body"])


def test_happy_path_creates_and_activates(wire):
    st = FakeSmartTyre(existing=[], after=[{"id": 34351}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    data = _body(resp)
    assert str(data["daijin_id"]) == "34351"
    assert data["status"] == "active"
    assert len(st.posts) == 1


def test_idempotent_when_already_in_dajin(wire):
    st = FakeSmartTyre(existing=[{"id": 888}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    assert str(_body(resp)["daijin_id"]) == "888"
    assert st.posts == []


def test_dajin_down_returns_pending(wire):
    st = FakeSmartTyre(fail=True)
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 202
    row = store.get_by_field(db, "tboxes", "tboxCode", "10B41D30EA79")
    assert row["status"] == "registering"
    assert row.get("daijin_id") is None


def test_invalid_tbox_code_returns_422(wire):
    wire(FakeSmartTyre())
    resp = mod.handler(_event(code="ZZZ"), None)
    assert resp["statusCode"] == 422

import json

import pytest

from functions.tires import create as mod


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

    def get_by_fields(self, db, table, filters):
        for r in self.rows.values():
            if all(r.get(k) == v for k, v in filters.items()):
                return dict(r)
        return None


class FakeSmartTyre:
    def __init__(self, after=None, fail=False):
        self._after = after or []
        self.fail = fail
        self.created = False
        self.posts = []

    def get(self, path, params):
        if self.fail:
            raise ConnectionError("Dajin down")
        return {"records": self._after if self.created else []}

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
        monkeypatch.setattr(mod, "get_by_fields", store.get_by_fields)
        monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)
        return store, db

    return setup


def _event(prefix="TSM", folio="9001", company=100, catalog=209, **extra):
    payload = {"prefix": prefix, "folio": folio, "company_id": company,
               "tires_catalog_id": catalog, **extra}
    return {"body": json.dumps(payload)}


def _body(resp):
    return json.loads(resp["body"])


def test_happy_path_creates_and_activates(wire):
    st = FakeSmartTyre(after=[{"id": 414997}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    data = _body(resp)
    assert str(data["daijin_id"]) == "414997"
    assert data["status"] == "new"  # status de negocio por defecto
    assert len(st.posts) == 1
    # tyreCode enviado a Dajin = id local
    assert st.posts[0][1]["tyreCode"] == str(data["id"])


def test_custom_business_status_passes_through(wire):
    st = FakeSmartTyre(after=[{"id": 1}])
    store, db = wire(st)
    resp = mod.handler(_event(status="used"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["status"] == "used"


def test_already_synced_locally_returns_ok(wire):
    st = FakeSmartTyre()
    store, db = wire(st)
    store.rows[1] = {"id": 1, "prefix": "TSM", "folio": "9001", "company_id": 100,
                     "daijin_id": 414997, "status": "new"}
    resp = mod.handler(_event(), None)
    assert resp["statusCode"] == 200
    assert st.posts == []  # ni habla con Dajin


def test_dajin_down_returns_pending(wire):
    st = FakeSmartTyre(fail=True)
    store, db = wire(st)
    resp = mod.handler(_event(), None)
    assert resp["statusCode"] == 202
    row = store.get_by_fields(db, "test_tires",
                              {"prefix": "TSM", "folio": "9001", "company_id": 100})
    assert row["status"] == "registering"
    assert row.get("daijin_id") is None


def test_missing_required_field_returns_422(wire):
    wire(FakeSmartTyre())
    resp = mod.handler({"body": json.dumps({"prefix": "TSM"})}, None)  # faltan campos
    assert resp["statusCode"] == 422

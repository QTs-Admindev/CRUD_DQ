import json

import pytest

from functions.vehicles import create as mod


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

    def get_where(self, db, table, where_sql, params=(), limit=200):
        # El handler busca por (unit_identifier, company_id, unit_catalog_id) en dos
        # variantes: filas VIVAS (live_sql) o soft-borradas (dead_sql, "is_deleted = 1").
        ident, cid, cat = params
        want_deleted = "is_deleted = 1" in where_sql
        out = []
        for r in self.rows.values():
            if (r.get("unit_identifier") == ident and r.get("company_id") == cid
                    and r.get("unit_catalog_id") == cat
                    and bool(r.get("is_deleted")) == want_deleted):
                out.append(dict(r))
        return out[:limit]


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

    def setup(st, catalog=None):
        # Catálogo de referencia (id 5) — el handler lo lee con get_by_id.
        store.rows[5] = catalog or {"id": 5, "name": "Tractocamión truck", "type": "motive", "d_id": 7}
        monkeypatch.setattr(mod, "get_db", lambda: db)
        monkeypatch.setattr(mod, "insert", store.insert)
        monkeypatch.setattr(mod, "update", store.update)
        monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
        monkeypatch.setattr(mod, "get_where", store.get_where)
        monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)
        return store, db

    return setup


def _event(unit="3MA0074S", company=100, catalog=5, **extra):
    payload = {"unit_identifier": unit, "company_id": company,
               "unit_catalog_id": catalog, **extra}
    return {"body": json.dumps(payload)}


def _body(resp):
    return json.loads(resp["body"])


def test_happy_path_creates_and_activates(wire):
    st = FakeSmartTyre(after=[{"id": 33369}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    data = _body(resp)
    assert str(data["daijin_id"]) == "33369"
    assert data["status"] == "active"
    assert len(st.posts) == 1
    # licensePlateNumber enviado a Dajin = id local; truck -> isTractor 1
    assert st.posts[0][1]["licensePlateNumber"] == str(data["id"])
    assert st.posts[0][1]["isTractor"] == 1


def test_trailer_type_maps_to_isTractor_2(wire):
    st = FakeSmartTyre(after=[{"id": 1}])
    store, db = wire(st, catalog={"id": 5, "name": "Caja seca", "type": "trailer", "d_id": 3})
    resp = mod.handler(_event(), None)
    assert resp["statusCode"] == 200
    assert st.posts[0][1]["isTractor"] == 2


def test_catalog_not_found_returns_422(wire):
    st = FakeSmartTyre(after=[{"id": 1}])
    store, db = wire(st)
    resp = mod.handler(_event(catalog=999), None)  # no existe en el catálogo
    assert resp["statusCode"] == 422


def test_dajin_down_returns_pending(wire):
    st = FakeSmartTyre(fail=True)
    store, db = wire(st)
    resp = mod.handler(_event(), None)
    assert resp["statusCode"] == 202
    rows = store.get_where(db, "test_units",
                           "unit_identifier = %s AND company_id = %s AND "
                           "unit_catalog_id = %s AND (is_deleted IS NULL OR is_deleted = 0)",
                           ["3MA0074S", 100, 5])
    row = rows[0]
    assert row["status"] == "registering"
    assert row.get("daijin_id") is None


def test_missing_required_field_returns_422(wire):
    wire(FakeSmartTyre())
    resp = mod.handler({"body": json.dumps({"unit_identifier": "X"})}, None)
    assert resp["statusCode"] == 422

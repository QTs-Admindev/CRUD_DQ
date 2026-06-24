import json

import pytest

from functions.sensors import create as mod


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeStore:
    """BD en memoria que imita shared.db.ops."""

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


def _event(code="A4C13873C3E6", company=100):
    return {"body": json.dumps({"sensor_code": code, "company_id": company})}


def _body(resp):
    return json.loads(resp["body"])


def test_happy_path_creates_and_activates(wire):
    st = FakeSmartTyre(existing=[], after=[{"id": 275771}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    data = _body(resp)
    assert str(data["daijin_id"]) == "275771"
    assert data["status"] == "active"
    assert len(st.posts) == 1  # se creó en Dajin


def test_idempotent_when_already_in_dajin(wire):
    # Dajin ya tiene el sensor -> se recupera el id, NO se recrea.
    st = FakeSmartTyre(existing=[{"id": 999}])
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    assert str(_body(resp)["daijin_id"]) == "999"
    assert st.posts == []  # no hubo POST


def test_already_synced_locally_returns_ok(wire):
    st = FakeSmartTyre()
    store, db = wire(st)
    store.rows[1] = {
        "id": 1, "sensorCode": "A4C13873C3E6", "company_id": 100,
        "daijin_id": 275771, "status": "active",
    }

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    assert st.posts == []  # ni siquiera habla con Dajin


def test_dajin_down_returns_pending_and_stays_registering(wire):
    st = FakeSmartTyre(fail=True)
    store, db = wire(st)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 202  # pending, no error
    # la fila local quedó en registering, sin daijin_id
    row = store.get_by_field(db, "sensors", "sensorCode", "A4C13873C3E6")
    assert row["status"] == "registering"
    assert row.get("daijin_id") is None


def test_invalid_sensor_code_returns_422(wire):
    wire(FakeSmartTyre())
    resp = mod.handler(_event(code="XYZ"), None)
    assert resp["statusCode"] == 422


def test_concurrent_duplicate_insert_resumes(wire, monkeypatch):
    # Carrera: el INSERT choca por clave duplicada -> se retoma la fila existente.
    st = FakeSmartTyre(existing=[], after=[{"id": 555}])
    store, db = wire(st)
    store.rows[77] = {
        "id": 77, "sensorCode": "A4C13873C3E6", "company_id": 100,
        "status": "registering", "daijin_id": None,
    }
    state = {"raced": False}

    def gbf(_db, _table, _field, value):
        return dict(store.rows[77]) if state["raced"] else None

    def failing_insert(_db, _table, _data):
        state["raced"] = True  # alguien más lo insertó en paralelo
        raise Exception("Duplicate entry 'A4C13873C3E6' for key 'sensorCode'")

    monkeypatch.setattr(mod, "get_by_field", gbf)
    monkeypatch.setattr(mod, "insert", failing_insert)

    resp = mod.handler(_event(), None)

    assert resp["statusCode"] == 200
    assert str(_body(resp)["daijin_id"]) == "555"
    assert store.rows[77]["status"] == "active"

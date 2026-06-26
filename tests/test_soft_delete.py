from functions.vehicles import delete as vdel
from functions.tires import delete as tdel


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self, rows, bound=False):
        self.rows = rows
        self.bound = bound          # what exists() returns (asset is bound or not)
        self.deleted_calls = []

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def soft_delete(self, db, table, rid):
        self.deleted_calls.append(rid)
        self.rows[rid]["is_deleted"] = 1
        return dict(self.rows[rid])

    def exists(self, db, table, filters):
        return self.bound


def _wire(monkeypatch, mod, store):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "soft_delete", store.soft_delete)
    monkeypatch.setattr(mod, "exists", store.exists, raising=False)


def _ev(rid):
    return {"pathParameters": {"id": str(rid)}}


def test_vehicle_soft_delete_happy(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None}})
    _wire(monkeypatch, vdel, store)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["is_deleted"] == 1
    assert store.deleted_calls == [1]


def test_vehicle_delete_blocked_when_bound(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None}}, bound=True)
    _wire(monkeypatch, vdel, store)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 409
    assert store.deleted_calls == []


def test_vehicle_not_found_404(monkeypatch):
    store = FakeStore({})
    _wire(monkeypatch, vdel, store)
    assert vdel.handler(_ev(99), None)["statusCode"] == 404


def test_already_deleted_is_idempotent(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 1}})
    _wire(monkeypatch, vdel, store)
    resp = vdel.handler(_ev(1), None)
    assert resp["statusCode"] == 200
    assert store.deleted_calls == []


def test_tire_soft_delete_happy(monkeypatch):
    store = FakeStore({5: {"id": 5, "is_deleted": 0, "unit_id": None, "sensor_id": None}})
    _wire(monkeypatch, tdel, store)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 200
    assert store.rows[5]["is_deleted"] == 1


def test_tire_delete_blocked_when_mounted_or_has_sensor(monkeypatch):
    store = FakeStore({5: {"id": 5, "is_deleted": 0, "unit_id": 1, "sensor_id": None}})
    _wire(monkeypatch, tdel, store)
    resp = tdel.handler(_ev(5), None)
    assert resp["statusCode"] == 409

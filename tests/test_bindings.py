import json

import pytest

from functions.bindings import bind_sensor, bind_tire, unbind_sensor, unbind_tire


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

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=200):
        # Guard de un-solo-dueño (sensor/tbox ya en otra llanta/unidad): 2 params, sin
        # otro dueño en estos tests -> [].
        if "sensor_id = %s" in where_sql or "tbox_id = %s" in where_sql:
            return []
        # bind_tire's position guard: unit_id, axle_index, wheel_index, exclude tire_id
        unit_id, axle, wheel, exclude_id = params
        return [dict(r) for r in self.rows.values()
                if r.get("unit_id") == unit_id and r.get("axle_index") == axle
                and r.get("wheel_index") == wheel and r.get("is_mounted") == 1
                and r.get("id") != exclude_id and not r.get("is_deleted")]


class FakeSmartTyre:
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, path, body):
        if self.fail:
            raise ConnectionError("Dajin down")
        self.posts.append((path, body))
        return None


def _wire(monkeypatch, module, store, db, st):
    monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setattr(module, "get_by_id", store.get_by_id)
    monkeypatch.setattr(module, "update", store.update)
    monkeypatch.setattr(module, "SmartTyreClient", lambda: st)
    if hasattr(module, "get_where"):
        monkeypatch.setattr(module, "get_where", store.get_where)
    if hasattr(module, "audit"):
        monkeypatch.setattr(module, "audit", lambda *a, **k: None)


def _seed():
    store = FakeStore()
    store.rows[1] = {"id": 1, "daijin_id": 33369, "status": "active",
                     "company_id": 100}  # unit
    store.rows[10] = {"id": 10, "is_mounted": 0, "unit_id": None, "sensor_id": None,
                      "axle_index": None, "wheel_index": None, "company_id": 100,
                      "daijin_id": 414997, "is_deleted": 0}  # tire
    store.rows[20] = {"id": 20, "sensorCode": "A4C13873C3E6", "daijin_id": 275771,
                      "company_id": 100}  # sensor
    return store


def _ev(path_id, body):
    return {"pathParameters": {"id": str(path_id)}, "body": json.dumps(body)}


def _body(resp):
    return json.loads(resp["body"])


def test_bind_tire_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4, "mount_position": 8}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["unit_id"] == 1
    assert store.rows[10]["is_mounted"] == 1
    # a Dajin se manda el daijin del vehículo y el id local como tyreCode
    assert st.posts[0][1]["vehicleId"] == 33369
    assert st.posts[0][1]["tyreCode"] == "10"


def test_bind_tire_already_mounted_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10]["is_mounted"] = 1
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []


def test_bind_tire_dajin_down_502(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre(fail=True)
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 502
    assert store.rows[10]["unit_id"] is None  # no se tocó local


def test_unbind_tire_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1})
    _wire(monkeypatch, unbind_tire, store, db, st)
    resp = unbind_tire.handler(_ev(1, {"tire_id": 10}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["unit_id"] is None
    assert store.rows[10]["is_mounted"] == 0


def test_bind_sensor_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1, "axle_index": 2, "wheel_index": 4})
    _wire(monkeypatch, bind_sensor, store, db, st)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["sensor_id"] == 20
    assert st.posts[0][1]["sensorCode"] == "A4C13873C3E6"
    assert st.posts[0][1]["vehicleId"] == 33369


def test_bind_sensor_unmounted_local_only(monkeypatch):
    # tire is NOT mounted (unit_id None): bind locally, never touch the platform.
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    _wire(monkeypatch, bind_sensor, store, db, st)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["sensor_id"] == 20          # bound locally
    assert st.posts == []                             # platform NOT called
    assert _body(resp)["synced_to_platform"] is False


def test_bind_tire_syncs_pending_sensor(monkeypatch):
    # mounting a tire that already has a locally-bound sensor syncs it to platform.
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10]["sensor_id"] = 20  # sensor bound locally while unmounted
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["is_mounted"] == 1
    paths = [p for p, _ in st.posts]
    assert "/smartyre/openapi/vehicle/tyre/bind" in paths
    assert "/smartyre/openapi/tyre/sensor/bind" in paths
    sensor_post = next(b for p, b in st.posts if p == "/smartyre/openapi/tyre/sensor/bind")
    assert sensor_post["sensorCode"] == "A4C13873C3E6"
    assert sensor_post["vehicleId"] == 33369


def test_bind_tire_company_mismatch_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10]["daijin_id"] = 111
    store.rows[10]["company_id"] = 200  # tire company differs from unit (100)
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []  # never reached the platform


def test_bind_tire_position_occupied_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10]["daijin_id"] = 111
    # another live, mounted tire already at axle 2 / wheel 4 on the same unit
    store.rows[11] = {"id": 11, "is_mounted": 1, "unit_id": 1, "company_id": 100,
                      "axle_index": 2, "wheel_index": 4, "is_deleted": 0}
    _wire(monkeypatch, bind_tire, store, db, st)
    resp = bind_tire.handler(_ev(1, {"tire_id": 10, "axle_index": 2, "wheel_index": 4}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []


def test_bind_sensor_not_ready_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1, "daijin_id": 111,
                           "axle_index": 2, "wheel_index": 4})
    store.rows[20]["daijin_id"] = None  # sensor not synced yet
    _wire(monkeypatch, bind_sensor, store, db, st)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []


def test_bind_sensor_company_mismatch_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1, "daijin_id": 111,
                           "axle_index": 2, "wheel_index": 4})
    store.rows[20]["company_id"] = 999  # sensor company differs from tire (100)
    _wire(monkeypatch, bind_sensor, store, db, st)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)
    assert resp["statusCode"] == 409
    assert st.posts == []


def test_unbind_sensor_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1, "sensor_id": 20})
    _wire(monkeypatch, unbind_sensor, store, db, st)
    resp = unbind_sensor.handler(_ev(10, {}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["sensor_id"] is None

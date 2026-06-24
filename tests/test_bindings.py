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


def _seed():
    store = FakeStore()
    store.rows[1] = {"id": 1, "daijin_id": 33369, "status": "active"}  # unit
    store.rows[10] = {"id": 10, "is_mounted": 0, "unit_id": None, "sensor_id": None,
                      "axle_index": None, "wheel_index": None}  # tire
    store.rows[20] = {"id": 20, "sensorCode": "A4C13873C3E6"}  # sensor
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


def test_bind_sensor_tire_not_mounted_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    _wire(monkeypatch, bind_sensor, store, db, st)
    resp = bind_sensor.handler(_ev(10, {"sensor_id": 20}), None)  # tire unit_id None
    assert resp["statusCode"] == 409
    assert st.posts == []


def test_unbind_sensor_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[10].update({"is_mounted": 1, "unit_id": 1, "sensor_id": 20})
    _wire(monkeypatch, unbind_sensor, store, db, st)
    resp = unbind_sensor.handler(_ev(10, {}), None)
    assert resp["statusCode"] == 200
    assert store.rows[10]["sensor_id"] is None

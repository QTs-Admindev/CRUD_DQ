import json

from functions.vehicles import update as mod


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self):
        # unit 1: synced (daijin_id), company 100, catalog 5, tbox 30
        self.rows = {
            "units": {1: {"id": 1, "unit_identifier": "OLD", "company_id": 100,
                          "unit_catalog_id": 5, "daijin_id": 33369, "tbox_id": 30}},
            "tires": {10: {"id": 10, "unit_id": 1, "company_id": 100, "sensor_id": 20,
                           "is_deleted": 0}},
            "sensors": {20: {"id": 20, "company_id": 100}},
            "tboxes": {30: {"id": 30, "company_id": 100, "tboxCode": "TBX30"}},
            "unit_catalog": {5: {"id": 5, "name": "truck", "type": "truck", "d_id": "7"},
                             9: {"id": 9, "name": "trailer", "type": "trailer", "d_id": "3"}},
        }

    def _tbl(self, table):
        # tolerate a TABLE_PREFIX (t("units") -> "units" here since prefix is empty)
        return self.rows.setdefault(table, {})

    def update(self, db, table, rid, data):
        self._tbl(table)[rid].update(data)
        return dict(self._tbl(table)[rid])

    def get_by_id(self, db, table, rid):
        r = self._tbl(table).get(rid)
        return dict(r) if r else None

    def get_where(self, db, table, where_sql, params=(), limit=200):
        # only used for the mounted-tires cascade (unit_id = %s ...)
        unit_id = params[0]
        return [dict(r) for r in self._tbl(table).values()
                if r.get("unit_id") == unit_id and not r.get("is_deleted")]


class FakeSmartTyre:
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, path, body):
        if self.fail:
            raise ConnectionError("platform down")
        self.posts.append((path, body))
        return None


def _wire(monkeypatch, store, st=None):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "get_where", store.get_where)
    monkeypatch.setattr(mod, "SmartTyreClient", lambda: st or FakeSmartTyre())
    monkeypatch.setattr(mod, "audit", lambda *a, **k: None)


def _ev(unit_id, body):
    return {"pathParameters": {"id": str(unit_id)}, "body": json.dumps(body)}


def test_update_unit_identifier_happy(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {"unit_identifier": "NEW"}), None)
    assert resp["statusCode"] == 200
    assert store.rows["units"][1]["unit_identifier"] == "NEW"


def test_not_found_404(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(999, {"unit_identifier": "X"}), None)
    assert resp["statusCode"] == 404


def test_persists_vin_and_mileage(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {"vin": "VIN123", "mileage": 4200}), None)
    assert resp["statusCode"] == 200
    assert store.rows["units"][1]["vin"] == "VIN123"
    assert store.rows["units"][1]["mileage"] == 4200


def test_model_change_validates_catalog_422(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {"unit_catalog_id": 999}), None)  # not in unit_catalog
    assert resp["statusCode"] == 422
    assert store.rows["units"][1]["unit_catalog_id"] == 5  # unchanged


def test_model_change_calls_platform_and_persists(monkeypatch):
    store = FakeStore()
    st = FakeSmartTyre()
    _wire(monkeypatch, store, st)
    resp = mod.handler(_ev(1, {"unit_catalog_id": 9}), None)
    assert resp["statusCode"] == 200
    # platform update called first with the unit's daijin_id and the tbox code
    assert st.posts and st.posts[0][0] == "/smartyre/openapi/vehicle/update"
    assert st.posts[0][1]["id"] == 33369
    assert st.posts[0][1]["tboxCode"] == "TBX30"
    assert store.rows["units"][1]["unit_catalog_id"] == 9


def test_model_change_platform_fails_502_no_db_write(monkeypatch):
    store = FakeStore()
    st = FakeSmartTyre(fail=True)
    _wire(monkeypatch, store, st)
    resp = mod.handler(_ev(1, {"unit_catalog_id": 9}), None)
    assert resp["statusCode"] == 502
    assert store.rows["units"][1]["unit_catalog_id"] == 5  # untouched


def test_company_change_cascades(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {"company_id": 200}), None)
    assert resp["statusCode"] == 200
    assert store.rows["units"][1]["company_id"] == 200
    assert store.rows["tires"][10]["company_id"] == 200   # mounted tire
    assert store.rows["sensors"][20]["company_id"] == 200  # its sensor
    assert store.rows["tboxes"][30]["company_id"] == 200   # the unit's tbox


def test_empty_body_is_noop_200(monkeypatch):
    store = FakeStore()
    _wire(monkeypatch, store)
    resp = mod.handler(_ev(1, {}), None)
    assert resp["statusCode"] == 200

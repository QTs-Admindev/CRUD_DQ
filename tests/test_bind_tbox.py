import json

from functions.vehicles import bind_tbox as mod


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


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


def _wire(monkeypatch, store, db, st):
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)


def _seed():
    store = FakeStore()
    store.rows[1] = {"id": 1, "daijin_id": "33369", "unit_catalog_id": 5,
                     "company_id": 100, "tbox_id": None}
    store.rows[20] = {"id": 20, "daijin_id": "34351", "tboxCode": "10B41D30EA79"}
    store.rows[5] = {"id": 5, "name": "Tractocamión truck", "type": "motive", "d_id": 7}
    return store


def _ev(unit_id, tbox_id):
    return {"pathParameters": {"id": str(unit_id)}, "body": json.dumps({"tbox_id": tbox_id})}


def test_assign_tbox_happy(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    _wire(monkeypatch, store, db, st)
    resp = mod.handler(_ev(1, 20), None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["tbox_id"] == 20
    # a Dajin: vehicle/update con el daijin del vehículo y el CÓDIGO del tbox
    # (la plataforma vincula por tboxCode, no por el id del tbox)
    assert st.posts[0][1]["id"] == "33369"
    assert st.posts[0][1]["tboxCode"] == "10B41D30EA79"


def test_tbox_not_synced_409(monkeypatch):
    store, db, st = _seed(), FakeDB(), FakeSmartTyre()
    store.rows[20]["daijin_id"] = None
    _wire(monkeypatch, store, db, st)
    resp = mod.handler(_ev(1, 20), None)
    assert resp["statusCode"] == 409
    assert st.posts == []

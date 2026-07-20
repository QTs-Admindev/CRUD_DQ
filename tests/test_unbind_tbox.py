from functions.vehicles import unbind_tbox as mod


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class FakeStore:
    def __init__(self):
        self.rows = {
            1: {"id": 1, "daijin_id": "33369", "unit_catalog_id": 5,
                "company_id": 100, "tbox_id": 20},
            5: {"id": 5, "name": "truck", "type": "motive", "d_id": 7},
        }

    def update(self, db, table, rid, data):
        self.rows[rid].update(data)
        return dict(self.rows[rid])

    def get_by_id(self, db, table, rid):
        r = self.rows.get(rid)
        return dict(r) if r else None


class FakeSmartTyre:
    def __init__(self):
        self.posts = []

    def post(self, path, body):
        self.posts.append((path, body))
        return None


def _wire(monkeypatch, store, st):
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "update", store.update)
    monkeypatch.setattr(mod, "SmartTyreClient", lambda: st)


def test_unassign_tbox_happy(monkeypatch):
    store, st = FakeStore(), FakeSmartTyre()
    _wire(monkeypatch, store, st)
    resp = mod.handler({"pathParameters": {"id": "1"}}, None)
    assert resp["statusCode"] == 200
    assert store.rows[1]["tbox_id"] is None
    assert st.posts[0][1]["tboxCode"] == ""  # se limpia por código, no por id


def test_no_tbox_409(monkeypatch):
    store, st = FakeStore(), FakeSmartTyre()
    store.rows[1]["tbox_id"] = None
    _wire(monkeypatch, store, st)
    resp = mod.handler({"pathParameters": {"id": "1"}}, None)
    assert resp["statusCode"] == 409
    assert st.posts == []

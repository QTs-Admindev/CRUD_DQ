import json

from functions.status import get_status as gs


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


def _wire(monkeypatch, rows):
    monkeypatch.setattr(gs, "get_db", lambda: FakeDB())
    monkeypatch.setattr(gs, "get_by_id",
                        lambda db, table, rid: (dict(rows[rid]) if rid in rows else None))


def _ev(resource, rid):
    return {"pathParameters": {"resource": resource, "id": str(rid)}}


def _body(resp):
    return json.loads(resp["body"])


def test_lifecycle_active(monkeypatch):
    _wire(monkeypatch, {1: {"id": 1, "is_deleted": 0, "daijin_id": "33", "status": "active"}})
    resp = gs.handler(_ev("vehicles", 1), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["lifecycle"] == "active"


def test_lifecycle_registering_when_no_daijin(monkeypatch):
    _wire(monkeypatch, {1: {"id": 1, "is_deleted": 0, "daijin_id": None, "status": "registering"}})
    assert _body(gs.handler(_ev("sensors", 1), None))["lifecycle"] == "registering"


def test_lifecycle_deleting_when_pending_remote(monkeypatch):
    _wire(monkeypatch, {1: {"id": 1, "is_deleted": 1, "daijin_id": "33", "status": "active"}})
    assert _body(gs.handler(_ev("tires", 1), None))["lifecycle"] == "deleting"


def test_lifecycle_deleted_when_daijin_cleared(monkeypatch):
    _wire(monkeypatch, {1: {"id": 1, "is_deleted": 1, "daijin_id": None, "status": "active"}})
    assert _body(gs.handler(_ev("tboxes", 1), None))["lifecycle"] == "deleted"


def test_resource_accepts_table_name(monkeypatch):
    _wire(monkeypatch, {1: {"id": 1, "is_deleted": 0, "daijin_id": "9", "status": "active"}})
    assert gs.handler(_ev("units", 1), None)["statusCode"] == 200


def test_invalid_resource_404(monkeypatch):
    _wire(monkeypatch, {})
    assert gs.handler(_ev("widgets", 1), None)["statusCode"] == 404


def test_invalid_id_400(monkeypatch):
    _wire(monkeypatch, {})
    assert gs.handler(_ev("vehicles", "abc"), None)["statusCode"] == 400


def test_not_found_404(monkeypatch):
    _wire(monkeypatch, {})
    assert gs.handler(_ev("vehicles", 99), None)["statusCode"] == 404

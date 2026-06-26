import json

from functions.lists import list_assets as mod


def test_invalid_resource_returns_404():
    resp = mod.handler({"pathParameters": {"resource": "secrets"}}, None)
    assert resp["statusCode"] == 404


def test_units_returns_rows(monkeypatch):
    def fake_get_many(db, table, cols, filters, limit=300):
        assert table == "units"  # TABLE_PREFIX vacío en test
        return [{"id": 1, "unit_identifier": "X", "daijin_id": 33369}]

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    resp = mod.handler({"pathParameters": {"resource": "units"}}, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])[0]["id"] == 1


def test_company_filter_parsed(monkeypatch):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    mod.handler({"pathParameters": {"resource": "tires"},
                 "queryStringParameters": {"company_id": "100"}}, None)
    assert seen["filters"] == {"is_deleted": 0, "company_id": 100}

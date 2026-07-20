import json

from functions.lists import list_assets as mod


def test_invalid_resource_returns_404():
    resp = mod.handler({"pathParameters": {"resource": "secrets"}}, None)
    assert resp["statusCode"] == 404


def test_units_returns_rows(monkeypatch):
    def fake_get_many(db, table, cols, filters, limit=300):
        assert table == "units"  # TABLE_PREFIX vacío en test
        assert "vin" in cols and "unit_catalog_id" in cols  # columnas para el FE
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


def test_admin_company_sees_all(monkeypatch):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    # company 2 is the admin -> no company filter (sees everything, incl. unassigned)
    mod.handler({"pathParameters": {"resource": "sensors"},
                 "queryStringParameters": {"company_id": "2"}}, None)
    assert seen["filters"] == {"is_deleted": 0}


def test_catalog_resources_are_unprefixed_and_unfiltered(monkeypatch):
    """Catálogos: tabla real (sin TABLE_PREFIX), sin is_deleted ni company_id."""
    import shared.config as config
    monkeypatch.setattr(config, "TABLE_PREFIX", "test_")
    monkeypatch.setattr(mod, "t", lambda name: "test_" + name)
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["table"] = table
        seen["filters"] = filters
        return [{"id": 1}]

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    for resource in ("unit_catalog", "tires_catalog", "companies"):
        resp = mod.handler({
            "pathParameters": {"resource": resource},
            "queryStringParameters": {"company_id": "100"},
        }, None)
        assert resp["statusCode"] == 200
        assert seen["table"] == resource  # SIN prefijo aunque TABLE_PREFIX exista
        assert seen["filters"] == {}     # sin is_deleted / company_id


def test_assets_keep_table_prefix(monkeypatch):
    """Los activos SÍ usan el prefijo (aislamiento test_)."""
    monkeypatch.setattr(mod, "t", lambda name: "test_" + name)
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["table"] = table
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    mod.handler({"pathParameters": {"resource": "units"}}, None)
    assert seen["table"] == "test_units"


def test_limit_param_respected_and_capped(monkeypatch):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    mod.handler({"pathParameters": {"resource": "units"},
                 "queryStringParameters": {"limit": "1000"}}, None)
    assert seen["limit"] == 1000

    mod.handler({"pathParameters": {"resource": "units"},
                 "queryStringParameters": {"limit": "999999"}}, None)
    assert seen["limit"] == mod.MAX_LIMIT

    resp = mod.handler({"pathParameters": {"resource": "units"},
                        "queryStringParameters": {"limit": "abc"}}, None)
    assert resp["statusCode"] == 422

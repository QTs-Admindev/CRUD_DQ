import json

from functions.lists import list_assets as mod


def test_invalid_resource_returns_404():
    resp = mod.handler({"pathParameters": {"resource": "secrets"}}, None)
    assert resp["statusCode"] == 404


def test_units_returns_rows(monkeypatch):
    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
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

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
        seen["filters"] = filters
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    mod.handler({"pathParameters": {"resource": "tires"},
                 "queryStringParameters": {"company_id": "100"}}, None)
    assert seen["filters"] == {"is_deleted": 0, "company_id": 100}


def test_admin_company_sees_all(monkeypatch):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
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

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
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

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
        seen["table"] = table
        return []

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)

    mod.handler({"pathParameters": {"resource": "units"}}, None)
    assert seen["table"] == "test_units"


def test_limit_param_respected_and_capped(monkeypatch):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
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


# ---------- modo paginado: saber si lo que llegó es TODO ----------

def _wire_paged(monkeypatch, rows, total):
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300, offset=0):
        seen["limit"] = limit
        seen["offset"] = offset
        seen["filters"] = dict(filters)
        return rows

    def fake_count(db, table, filters=None):
        seen["count_filters"] = dict(filters or {})
        return total

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)
    monkeypatch.setattr(mod, "count_rows", fake_count)
    return seen


def test_paged_returns_total_and_window(monkeypatch):
    seen = _wire_paged(monkeypatch, [{"id": 9}], total=3400)

    resp = mod.handler({"pathParameters": {"resource": "tires"},
                        "queryStringParameters": {"paged": "1", "limit": "1000",
                                                  "offset": "2000"}}, None)

    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["total"] == 3400          # el consumidor sabe que faltan páginas
    assert body["limit"] == 1000 and body["offset"] == 2000
    assert body["data"] == [{"id": 9}]
    assert seen["offset"] == 2000
    # El total se cuenta con LOS MISMOS filtros que la página, si no mentiría.
    assert seen["count_filters"] == seen["filters"]


def test_without_paged_the_response_is_the_plain_array(monkeypatch):
    # Compatibilidad: quien ya consume el endpoint no se entera del cambio.
    _wire_paged(monkeypatch, [{"id": 1}], total=1)

    resp = mod.handler({"pathParameters": {"resource": "tires"}}, None)

    assert json.loads(resp["body"]) == [{"id": 1}]


def test_offset_must_be_an_integer(monkeypatch):
    _wire_paged(monkeypatch, [], total=0)
    resp = mod.handler({"pathParameters": {"resource": "tires"},
                        "queryStringParameters": {"offset": "abc"}}, None)
    assert resp["statusCode"] == 422


def test_paged_params_are_not_treated_as_column_filters(monkeypatch):
    seen = _wire_paged(monkeypatch, [], total=0)
    mod.handler({"pathParameters": {"resource": "tires"},
                 "queryStringParameters": {"paged": "1", "offset": "10"}}, None)
    assert "paged" not in seen["filters"] and "offset" not in seen["filters"]

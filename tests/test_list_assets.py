"""
Listado generico de activos (`GET /list/{resource}`).

HALLAZGO ORIGINAL: el `company_id` venia del query string y `ADMIN_COMPANY_ID = 2`
DESACTIVABA el filtro. O sea que `?company_id=2` devolvia el inventario completo
de toda la flota, y `?company_id=<cualquiera>` devolvia el de esa empresa. Sin
autenticacion, bastaba con conocer el numero.

CORREGIDO: el alcance sale de las claims verificadas del token. Un usuario normal
queda anclado a su empresa; solo el grupo admin conserva la vista global, y el
parametro pasa a ser un filtro para el admin, no un interruptor para cualquiera.
"""

import json

import pytest

from functions.lists import list_assets as mod
from tests.conftest import as_admin, as_company, authed


@pytest.fixture
def capture(monkeypatch):
    """Sustituye la capa de datos y devuelve lo que recibio `get_many`."""
    seen = {}

    def fake_get_many(db, table, cols, filters, limit=300):
        seen["table"] = table
        seen["cols"] = cols
        seen["filters"] = filters
        seen["limit"] = limit
        return [{"id": 1, "unit_identifier": "X", "daijin_id": 33369}]

    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_many", fake_get_many)
    return seen


def _ev(resource, qs=None, identity=None):
    ev = {"pathParameters": {"resource": resource}}
    if qs is not None:
        ev["queryStringParameters"] = qs
    return authed(ev, identity)


# ---------------------------------------------------------------------------
# Contrato basico
# ---------------------------------------------------------------------------

def test_invalid_resource_returns_404():
    assert mod.handler(_ev("secrets"), None)["statusCode"] == 404


def test_units_returns_rows(capture):
    resp = mod.handler(_ev("units"), None)

    assert resp["statusCode"] == 200
    assert capture["table"] == "units"  # TABLE_PREFIX vacio en test
    assert "vin" in capture["cols"] and "unit_catalog_id" in capture["cols"]
    assert json.loads(resp["body"])[0]["id"] == 1


def test_assets_keep_table_prefix(capture, monkeypatch):
    """Los activos SI usan el prefijo (aislamiento test_)."""
    monkeypatch.setattr(mod, "t", lambda name: "test_" + name)

    mod.handler(_ev("units"), None)

    assert capture["table"] == "test_units"


def test_catalog_resources_are_unprefixed_and_unfiltered(capture, monkeypatch):
    """Catalogos: tabla real (sin TABLE_PREFIX), sin is_deleted ni company_id."""
    import shared.config as config
    monkeypatch.setattr(config, "TABLE_PREFIX", "test_")
    monkeypatch.setattr(mod, "t", lambda name: "test_" + name)

    for resource in ("unit_catalog", "tires_catalog", "companies"):
        resp = mod.handler(_ev(resource, {"company_id": "100"}), None)
        assert resp["statusCode"] == 200
        assert capture["table"] == resource   # SIN prefijo aunque TABLE_PREFIX exista
        assert capture["filters"] == {}       # sin is_deleted / company_id


def test_limit_param_respected_and_capped(capture):
    mod.handler(_ev("units", {"limit": "1000"}), None)
    assert capture["limit"] == 1000

    mod.handler(_ev("units", {"limit": "999999"}), None)
    assert capture["limit"] == mod.MAX_LIMIT

    resp = mod.handler(_ev("units", {"limit": "abc"}), None)
    assert resp["statusCode"] == 422


def test_company_id_no_numerico_es_422(capture):
    resp = mod.handler(_ev("units", {"company_id": "abc"}), None)
    assert resp["statusCode"] == 422


# ---------------------------------------------------------------------------
# Alcance por empresa derivado del token
# ---------------------------------------------------------------------------

def test_usuario_normal_queda_anclado_a_su_empresa(capture):
    """Sin pedir nada, el filtro sale del token."""
    mod.handler(_ev("tires", identity=as_company(100)), None)

    assert capture["filters"] == {"is_deleted": 0, "company_id": 100}


def test_usuario_normal_no_puede_pedir_otra_empresa(capture):
    """HALLAZGO CERRADO: iterar `company_id` ya no funciona."""
    resp = mod.handler(
        _ev("tires", {"company_id": "999"}, identity=as_company(100)), None
    )

    assert resp["statusCode"] == 403
    assert "filters" not in capture, "no query may run for a rejected scope"


def test_usuario_normal_pidiendo_la_empresa_admin_no_ve_todo(capture):
    """
    HALLAZGO CERRADO Y CRITICO: `?company_id=2` (ADMIN_COMPANY_ID) desactivaba
    el filtro por completo, asi que cualquiera que conociera el numero leia el
    inventario de toda la flota. Ahora es un 403 como cualquier otra empresa
    ajena.
    """
    resp = mod.handler(
        _ev("sensors", {"company_id": "2"}, identity=as_company(100)), None
    )

    assert resp["statusCode"] == 403
    assert "filters" not in capture


def test_el_numero_dos_ya_no_es_un_interruptor(capture):
    """
    Contraprueba del mismo hallazgo desde el otro lado: para el usuario de la
    empresa 2, el 2 se aplica como filtro normal, no como "ver todo".
    """
    mod.handler(_ev("sensors", {"company_id": "2"}, identity=as_company(2)), None)

    assert capture["filters"] == {"is_deleted": 0, "company_id": 2}, (
        "company 2 must be filtered like any other company"
    )


def test_usuario_normal_puede_pedir_su_propia_empresa(capture):
    mod.handler(_ev("tires", {"company_id": "100"}, identity=as_company(100)), None)

    assert capture["filters"] == {"is_deleted": 0, "company_id": 100}


def test_admin_sin_parametro_ve_todo(capture):
    """El staff conserva la vista global, incluido el inventario sin asignar."""
    mod.handler(_ev("sensors", identity=as_admin()), None)

    assert capture["filters"] == {"is_deleted": 0}, "no company filter for staff"


def test_admin_puede_acotar_a_una_empresa(capture):
    """Para el staff el parametro sigue siendo un filtro util."""
    mod.handler(_ev("tires", {"company_id": "100"}, identity=as_admin()), None)

    assert capture["filters"] == {"is_deleted": 0, "company_id": 100}


def test_sin_autorizador_no_se_lista_nada(capture):
    """Falla cerrado: sin claims es 401, nunca "todas las empresas"."""
    resp = mod.handler({"pathParameters": {"resource": "tires"}}, None)

    assert resp["statusCode"] == 401
    assert "filters" not in capture


def test_token_sin_empresa_ni_grupo_admin_es_403(capture):
    """Un token valido pero sin tenant no se mapea a los datos de nadie."""
    from tests.conftest import auth_context

    resp = mod.handler(
        authed({"pathParameters": {"resource": "tires"}},
               auth_context(email="huerfano@demo.mx")),
        None,
    )

    assert resp["statusCode"] == 403
    assert "filters" not in capture


def test_los_catalogos_tambien_exigen_identidad(capture):
    """Los catalogos son datos de referencia compartidos, pero no publicos."""
    resp = mod.handler({"pathParameters": {"resource": "companies"}}, None)

    assert resp["statusCode"] == 401
    assert "filters" not in capture


def test_la_bitacora_de_auditoria_se_acota_por_empresa(capture):
    """asset_audit_log lleva company_id: un cliente solo ve su propia bitacora."""
    mod.handler(_ev("asset_audit_log", identity=as_company(100)), None)

    assert capture["filters"]["company_id"] == 100


def test_el_filtro_de_borrado_logico_no_se_puede_sobrescribir(capture):
    """Ni siquiera el admin puede pedir ver filas borradas por query string."""
    mod.handler(_ev("tires", {"is_deleted": "1"}, identity=as_admin()), None)

    assert capture["filters"]["is_deleted"] == 0

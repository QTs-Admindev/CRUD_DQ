"""
Autenticacion, alcance por empresa y actor de la bitacora.

Cubre los tres hallazgos de esta rama:

1. Ninguna de las 31 rutas tenia autorizador: la API entera era publica.
2. `functions/lists/list_assets.py` tomaba el `company_id` del cliente y
   `ADMIN_COMPANY_ID = 2` desactivaba el filtro (probado en
   `tests/test_list_assets.py`).
3. `shared/audit.py` tomaba el actor de la cabecera `X-Actor`, que el propio
   cliente escribia: cualquiera podia atribuir cualquier accion a cualquiera,
   lo que deja la bitacora sin valor probatorio.

Aqui NO se usan dobles de `shared/auth`: se fabrica el evento de Lambda tal como
lo entrega API Gateway, para probar la extraccion real de las claims.
"""

from pathlib import Path

import pytest
import yaml

from shared import audit as audit_mod
from shared import auth
from shared.auth import AuthError
from tests.conftest import as_admin, as_company, auth_context

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Extraccion de claims
# ---------------------------------------------------------------------------

def test_las_claims_se_leen_del_contexto_del_autorizador():
    claims = auth.claims_from(as_company(100, email="u@cliente.mx"))

    assert claims["custom:company_id"] == "100"
    assert claims["email"] == "u@cliente.mx"


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"requestContext": {}},
        {"requestContext": {"authorizer": {}}},
        {"requestContext": {"authorizer": {"claims": {}}}},
        {"requestContext": {"authorizer": {"claims": None}}},
    ],
)
def test_sin_contexto_de_autorizador_se_falla_cerrado(event):
    """La ausencia de autorizador NUNCA puede significar "todas las empresas"."""
    with pytest.raises(AuthError) as exc:
        auth.claims_from(event)

    assert exc.value.status == 401


def test_una_cabecera_no_autentica():
    """Un evento con cabeceras pero sin claims sigue siendo anonimo."""
    event = {"headers": {"X-Actor": "jefe@quinta.tech", "Authorization": "Bearer x"}}

    with pytest.raises(AuthError) as exc:
        auth.claims_from(event)

    assert exc.value.status == 401


# ---------------------------------------------------------------------------
# Rol de administrador
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "groups", ["quinta-admin", "quinta-admin,clientes", "clientes quinta-admin"]
)
def test_el_grupo_admin_se_reconoce_en_sus_formatos(groups):
    assert auth.is_admin(auth_context(groups=groups)) is True


@pytest.mark.parametrize("groups", [None, "", "clientes", "quinta-administradores"])
def test_sin_el_grupo_admin_no_hay_privilegio(groups):
    assert auth.is_admin(auth_context(company_id=100, groups=groups)) is False


def test_require_admin_rechaza_a_un_usuario_normal():
    with pytest.raises(AuthError) as exc:
        auth.require_admin(as_company(100))

    assert exc.value.status == 403


def test_require_admin_deja_pasar_al_staff():
    assert auth.require_admin(as_admin()) is None


# ---------------------------------------------------------------------------
# Alcance por empresa
# ---------------------------------------------------------------------------

def test_el_alcance_de_un_usuario_es_el_de_su_token():
    assert auth.resolve_company_scope(as_company(100)) == 100


def test_pedir_la_propia_empresa_es_valido():
    assert auth.resolve_company_scope(as_company(100), 100) == 100


def test_pedir_otra_empresa_es_403():
    with pytest.raises(AuthError) as exc:
        auth.resolve_company_scope(as_company(100), 999)

    assert exc.value.status == 403


def test_pedir_la_empresa_admin_ya_no_abre_el_inventario_completo():
    """
    HALLAZGO CERRADO: `ADMIN_COMPANY_ID = 2` era un bypass basado en un numero
    que cualquiera podia escribir. Ahora el 2 no tiene ningun poder especial.
    """
    with pytest.raises(AuthError) as exc:
        auth.resolve_company_scope(as_company(100), 2)

    assert exc.value.status == 403


def test_el_privilegio_viene_del_grupo_no_del_numero_de_empresa():
    """
    Un usuario cuya empresa ES la 2 pero que no esta en el grupo admin queda
    acotado a la 2, no ve todo.
    """
    assert auth.resolve_company_scope(as_company(2)) == 2


def test_el_staff_sin_peticion_explicita_tiene_alcance_global():
    assert auth.resolve_company_scope(as_admin()) is None


def test_el_staff_puede_acotar_a_cualquier_empresa():
    assert auth.resolve_company_scope(as_admin(), 999) == 999


def test_un_token_sin_empresa_ni_grupo_admin_no_alcanza_nada():
    with pytest.raises(AuthError) as exc:
        auth.resolve_company_scope(auth_context(email="huerfano@demo.mx"))

    assert exc.value.status == 403


def test_una_claim_de_empresa_no_numerica_es_403():
    """Una claim corrupta falla cerrada, no se degrada a alcance global."""
    with pytest.raises(AuthError) as exc:
        auth.resolve_company_scope(auth_context(company_id="cien"))

    assert exc.value.status == 403


def test_auth_error_lleva_el_estado_http():
    """Los handlers hacen `except AuthError as e: return error(e.status, ...)`."""
    err = AuthError(403, "Empresa fuera de tu alcance")

    assert err.status == 403
    assert str(err) == "Empresa fuera de tu alcance"


# ---------------------------------------------------------------------------
# Actor de la bitacora
# ---------------------------------------------------------------------------

def test_el_actor_sale_del_token():
    assert auth.actor_from(as_company(100, email="ana@cliente.mx")) == "ana@cliente.mx"


def test_la_cabecera_x_actor_ya_no_decide_el_actor():
    """
    HALLAZGO CERRADO: el cliente se autodeclaraba el actor. Aqui la cabecera
    miente y el token dice la verdad; gana el token.
    """
    event = as_company(100, email="ana@cliente.mx")
    event["headers"] = {"X-Actor": "director@quinta.tech"}

    assert auth.actor_from(event) == "ana@cliente.mx", (
        "the self-declared header must never override the verified identity"
    )


def test_la_cabecera_x_actor_no_sirve_sin_token():
    """Sin claims, declararse actor por cabecera no funciona: queda 'system'."""
    event = {"headers": {"X-Actor": "director@quinta.tech"}}

    assert auth.actor_from(event) == "system"


def test_el_actor_cae_a_username_o_sub_si_no_hay_correo():
    """Un token sin `email` sigue identificando a alguien concreto."""
    assert auth.actor_from(auth_context(**{"cognito:username": "ana"})) == "ana"
    assert auth.actor_from(auth_context(sub="uuid-123")) == "uuid-123"


def test_las_invocaciones_no_http_siguen_siendo_system():
    """El cron de reconciliacion y el worker de bulk no tienen requestContext."""
    assert auth.actor_from({"ids": [1, 2], "pass": 1}) == "system"
    assert auth.actor_from(None) == "system"


def test_audit_reexporta_actor_from_desde_auth():
    """`shared/audit` conserva el nombre publico, pero delega la resolucion."""
    assert audit_mod.actor_from is auth.actor_from


def test_audit_usa_la_identidad_del_token(monkeypatch):
    """La fila de bitacora se escribe con el actor derivado del token."""
    rows = []
    monkeypatch.setattr(audit_mod, "insert",
                        lambda db, table, data: rows.append((table, data)))

    class _DB:
        def commit(self): pass
        def rollback(self): pass

    event = as_company(100, email="ana@cliente.mx")
    event["headers"] = {"X-Actor": "director@quinta.tech"}

    audit_mod.audit(_DB(), event, None, action="update", asset_type="tire",
                    asset_id=5)

    assert rows, "the audit row must be written"
    table, data = rows[0]
    assert table == "asset_audit_log"
    assert data["actor"] == "ana@cliente.mx", "actor comes from the token"


def test_audit_sigue_sin_romper_la_operacion_que_registra(monkeypatch):
    """Contraprueba: la bitacora es best-effort y nunca propaga su error."""
    def boom(*a, **k):
        raise RuntimeError("audit table missing")

    monkeypatch.setattr(audit_mod, "insert", boom)

    class _DB:
        def __init__(self): self.rollbacks = 0
        def commit(self): pass
        def rollback(self): self.rollbacks += 1

    db = _DB()
    audit_mod.audit(db, as_admin(), None, action="update", asset_type="tire")

    assert db.rollbacks == 1, "a failed audit rolls back its own transaction"


# ---------------------------------------------------------------------------
# Cableado en serverless.yml
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def serverless():
    return yaml.safe_load((ROOT / "serverless.yml").read_text(encoding="utf-8"))


def test_todas_las_rutas_http_llevan_autorizador_cognito(serverless):
    """
    HALLAZGO CERRADO: ninguna de las rutas tenia autorizador. Se verifica una a
    una para que anadir un endpoint sin autorizador rompa la suite.
    """
    sin_autorizador = []
    for name, fn in serverless["functions"].items():
        for event in fn.get("events", []):
            http = event.get("http")
            if http is None:
                continue
            authorizer = http.get("authorizer")
            if not authorizer or authorizer.get("type") != "COGNITO_USER_POOLS":
                sin_autorizador.append(f"{name} {http.get('method')} {http.get('path')}")

    assert sin_autorizador == [], f"unauthenticated endpoints: {sin_autorizador}"


def test_se_cubren_todas_las_rutas_existentes(serverless):
    """Cuenta explicita: si aparecen rutas nuevas hay que revisarlas a conciencia."""
    total = sum(
        1 for fn in serverless["functions"].values()
        for e in fn.get("events", []) if "http" in e
    )

    assert total == 31, f"the endpoint count changed ({total}); re-check the authorizer"


def test_los_eventos_referencian_el_recurso_del_autorizador(serverless):
    for name, fn in serverless["functions"].items():
        for event in fn.get("events", []):
            if "http" not in event:
                continue
            ref = event["http"]["authorizer"]["authorizerId"]
            assert ref == {"Ref": "ApiGatewayAuthorizer"}, f"{name} points elsewhere"


def test_el_autorizador_esta_declarado_como_recurso(serverless):
    props = serverless["resources"]["Resources"]["ApiGatewayAuthorizer"]["Properties"]

    assert props["Type"] == "COGNITO_USER_POOLS"
    assert props["IdentitySource"] == "method.request.header.Authorization"
    assert props["ProviderARNs"], "the user pool ARN must be provided"


def test_las_funciones_sin_http_no_quedan_expuestas(serverless):
    """El worker de bulk y el cron se invocan internamente, no por HTTP."""
    for name in ("sensorsBulkSyncWorker", "reconcile"):
        events = serverless["functions"][name].get("events", [])
        assert not any("http" in e for e in events), f"{name} must not be public"


def test_shared_viaja_en_el_paquete_de_despliegue(serverless):
    """Si `shared/**` no se empaqueta, shared/auth.py no llega a las Lambdas."""
    assert "shared/**" in serverless["package"]["patterns"]

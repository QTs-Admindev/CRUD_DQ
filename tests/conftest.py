import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Identidad: eventos con las claims verificadas del autorizador Cognito
# ---------------------------------------------------------------------------
# API Gateway valida el JWT y entrega las claims en
# `event.requestContext.authorizer.claims`. Estos helpers construyen ese evento
# tal cual, de modo que las pruebas ejercitan la extraccion real de
# `shared/auth.py` en vez de un doble.

ADMIN_GROUP = "quinta-admin"


def auth_context(company_id=None, groups=None, email=None, **extra):
    """El bloque `requestContext` que anade el autorizador Cognito."""
    claims = dict(extra)
    if company_id is not None:
        claims["custom:company_id"] = str(company_id)
    if groups is not None:
        claims["cognito:groups"] = groups
    if email is not None:
        claims["email"] = email
    return {"requestContext": {"authorizer": {"claims": claims}}}


def as_company(company_id, email=None, **extra):
    """Evento de un usuario normal anclado a una empresa."""
    return auth_context(
        company_id=company_id,
        email=email or f"user{company_id}@cliente.mx",
        **extra,
    )


def as_admin(email="staff@quinta.tech", company_id=None, **extra):
    """Evento del staff de Quinta (alcance global)."""
    return auth_context(
        company_id=company_id, groups=ADMIN_GROUP, email=email, **extra
    )


def authed(event, identity=None):
    """Fusiona un evento de prueba con una identidad (admin por defecto)."""
    merged = dict(event or {})
    merged.update(identity if identity is not None else as_admin())
    return merged

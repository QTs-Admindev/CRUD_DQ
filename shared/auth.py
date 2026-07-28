"""Identity derived from the Cognito authorizer.

API Gateway validates the JWT before the Lambda runs (see the
`COGNITO_USER_POOLS` authorizer in `serverless.yml`) and hands the *verified*
claims to the handler in `event.requestContext.authorizer.claims`.

Everything that answers "who is this and what may they see" comes from there.
Never from a query parameter and never from a request header: both are chosen
freely by the caller and are therefore not authorization decisions.

Two roles:
  * a regular user is pinned to the `custom:company_id` in their token;
  * a member of the admin group (Quinta staff / the provider company) sees the
    whole inventory, including unassigned sensors and tboxes.

Everything fails closed: no authorizer context means unauthenticated, never
"all companies".
"""

import os

# Custom attribute carrying the tenant in the Cognito user pool.
COMPANY_CLAIM = os.environ.get("COMPANY_ID_CLAIM", "custom:company_id")

# Cognito group whose members administer the whole platform.
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "quinta-admin")


class AuthError(Exception):
    """Raised when the caller cannot be authenticated or is out of scope.

    Carries the HTTP status the handler should answer with, so handlers stay a
    single `except AuthError as e: return error(e.status, str(e))`.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def claims_from(event) -> dict:
    """Return the verified Cognito claims, or raise AuthError(401)."""
    request_context = (event or {}).get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims")

    if not isinstance(claims, dict) or not claims:
        # Either the gateway authorizer is not attached to this route or the
        # function was invoked directly. Both are failures, not free passes.
        raise AuthError(401, "No autenticado")

    return claims


def actor_from(event) -> str:
    """The authenticated identity, for the audit trail.

    Previously this read the `X-Actor` header, which the client wrote itself:
    anyone could attribute an action to anyone. Now it comes from the token.
    Falls back to "system" only for non-HTTP invocations (the reconciliation
    cron and the bulk sync worker), which carry no requestContext at all.
    """
    try:
        claims = claims_from(event)
    except AuthError:
        return "system"
    return (claims.get("email") or claims.get("cognito:username")
            or claims.get("sub") or "system")


def is_admin(event) -> bool:
    """True when the caller belongs to the platform admin group."""
    groups = claims_from(event).get("cognito:groups", "")
    if isinstance(groups, str):
        groups = groups.replace(",", " ").split()
    return ADMIN_GROUP in groups


def company_id_from(event):
    """The company the token is pinned to, or None when the claim is absent."""
    raw = claims_from(event).get(COMPANY_CLAIM)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise AuthError(403, "Claim de empresa inválida en el token")


def require_admin(event) -> None:
    """Guard for platform-wide operations."""
    if not is_admin(event):
        raise AuthError(403, "Se requiere acceso de administrador")


def resolve_company_scope(event, requested=None):
    """Decide which company a listing must be restricted to.

    Returns the company_id to filter by, or None meaning "no restriction",
    which only an admin can ever obtain.

    * Admin: `requested` is honoured as-is, including None for a global view.
    * Regular user: always pinned to the token's company. Asking for a
      different one raises, it is not silently ignored.
    """
    own = company_id_from(event)

    if is_admin(event):
        return requested

    if own is None:
        raise AuthError(403, "El token no tiene empresa asignada")

    if requested is not None and requested != own:
        raise AuthError(403, "Empresa fuera de tu alcance")

    return own

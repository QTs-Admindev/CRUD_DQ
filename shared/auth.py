"""Alcance por compañía para los endpoints con datos de flota.

Mismo modelo que list_assets: la compañía admin (2) ve TODO el inventario
(incluido lo no asignado); cualquier otra compañía queda acotada a sus propias
filas. No hay identidad de llamador separada del company_id solicitado — igual
que list_assets, el alcance se deriva del company_id que llega en la petición.
"""
from shared.config import ADMIN_COMPANY_ID


def resolve_company_scope(event, requested_company_id):
    """Devuelve el company_id efectivo por el que filtrar, o None para alcance global.

    - requested_company_id None  -> None (admin sin filtro: ve todo).
    - requested_company_id admin -> None (la compañía admin ve todo).
    - cualquier otra compañía     -> ese company_id (acotado a sus filas).

    `event` se recibe para paridad con list_assets y para futuras fuentes de
    identidad (claims del authorizer); hoy el alcance sale del company_id pedido.
    """
    if requested_company_id is None:
        return None
    if requested_company_id == ADMIN_COMPANY_ID:
        return None
    return requested_company_id

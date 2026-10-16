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


SOLO_ADMIN = "Solo Quinta puede crear, editar o borrar sensores, Qbox y paquetes. Tu compañía puede asignarlos y desasignarlos."


def compania_que_llama(event):
    """La compañía de quien opera, del header X-Company que manda el FE (la de su
    cuenta). None si no viene o no es un entero."""
    headers = (event or {}).get("headers") or {}
    valor = headers.get("X-Company") or headers.get("x-company")
    try:
        return int(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


def solo_admin(event):
    """403 si quien llama es de una compañía que no es la admin; None si puede.

    Sin el header no se bloquea: llamadas internas entre funciones y el FE que aún
    no lo manda. No es autenticación, igual que X-Actor: evita que la pantalla o un
    cliente del API de una compañía cree o borre inventario que solo opera Quinta.
    """
    from shared.utils.response import error

    quien = compania_que_llama(event)
    if quien is not None and quien != ADMIN_COMPANY_ID:
        return error(403, SOLO_ADMIN)
    return None

"""Llanta genérica: la fila centinela de tires_catalog para una llanta de la que no
se conocen marca, modelo ni medida (checkbox "Desconocida" del FE).

Es la MISMA convención que usa el FE (services/crudDq.ts): la marca "Desconocida",
o el id de GENERIC_TIRES_CATALOG_ID si viene en el entorno.
"""
import os

# (brand, model, size, position) de la fila centinela.
GENERIC_SENTINEL = ("Desconocida", "DESCONOCIDA", "DESCONOCIDA", "ALL")


def _override_id():
    v = os.environ.get("GENERIC_TIRES_CATALOG_ID")
    return int(v) if v else None


def resolve_generic_catalog_id(db, get_where):
    """Id de la fila genérica de tires_catalog, o None si no existe.

    1) Si GENERIC_TIRES_CATALOG_ID viene en el entorno, se respeta (override).
    2) Si no, se busca por la fila centinela.
    """
    override = _override_id()
    if override:
        return override
    brand, model, size, position = GENERIC_SENTINEL
    rows = get_where(
        db, "tires_catalog",
        "brand = %s AND model = %s AND size = %s AND position = %s",
        [brand, model, size, position], 1)
    return rows[0]["id"] if rows else None


def is_generic_catalog(row) -> bool:
    """¿Esta fila de tires_catalog es la genérica? Misma regla que el FE."""
    if not row:
        return False
    override = _override_id()
    if override is not None and row.get("id") == override:
        return True
    return str(row.get("brand") or "").strip().lower() == GENERIC_SENTINEL[0].lower()

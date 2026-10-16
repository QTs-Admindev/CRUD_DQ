"""Operadores (drivers) asignados a unidades.

La tabla `drivers` la administra CRUDTires (/api/drivers); aquí solo se liga un
operador a una unidad. `driver_assignments` guarda el historial: la fila vigente
es la que tiene released_at NULL, y dos índices UNIQUE impiden que una unidad
tenga dos operadores o un operador dos unidades a la vez.
"""
import logging

import pymysql

from shared.config import t

_log = logging.getLogger("crud_dq")

# Tabla real de CRUDTires, sin prefijo de entorno.
DRIVERS = "drivers"


def asignaciones():
    return t("driver_assignments")


def nombre_de(driver: dict | None) -> str | None:
    if not driver:
        return None
    partes = [driver.get(k) for k in ("name", "last_name", "second_last_name")]
    return " ".join(p for p in partes if p) or driver.get("phone_number")


def vigente(db, campo: str, valor) -> dict | None:
    """La asignación vigente de una unidad (campo='unit_id') o de un operador."""
    with db.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(f"SELECT * FROM {asignaciones()} WHERE {campo} = %s "
                    "AND released_at IS NULL", [valor])
        return cur.fetchone()


def soltar(db, campo: str, valor, cuando: int, quien: str | None) -> int:
    """Cierra la asignación vigente (sin commit). Devuelve cuántas cerró."""
    with db.cursor() as cur:
        cur.execute(f"UPDATE {asignaciones()} SET released_at = %s, released_by = %s "
                    f"WHERE {campo} = %s AND released_at IS NULL", [cuando, quien, valor])
        return cur.rowcount


def soltar_unidad_borrada(db, unit_id, cuando: int, quien: str | None) -> None:
    """Al borrar una unidad su operador queda libre. Nunca rompe el borrado: si
    falla, la asignación de la unidad borrada se ignora al reasignar."""
    try:
        soltar(db, "unit_id", unit_id, cuando, quien)
    except Exception as e:
        _log.warning("no se liberó el operador de la unidad %s: %s", unit_id, e)

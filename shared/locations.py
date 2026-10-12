"""Sedes y almacenes: reglas compartidas por sus endpoints.

Una sede (`sites`) es un lugar físico de una compañía; un almacén (`warehouses`)
vive en una sede (o en ninguna) y guarda inventario. Las unidades se asignan a
una sede (`units.site_id`); llantas, sensores y Qbox a un almacén
(`warehouse_id`). La ubicación es "a dónde pertenece" el activo: montar una
llanta o ligar un Qbox no la borra, así al desmontarlo se sabe a dónde vuelve.

Alcance: el mismo modelo que list_assets. La compañía admin (2) ve y opera las
sedes y almacenes de todas las compañías, además de los suyos; cualquier otra
solo los suyos. No hay identidad del llamador: el alcance sale del `company_id`
que manda el FE (query string en GET/PUT/DELETE, cuerpo en POST).
"""
import pymysql

from shared.config import ADMIN_COMPANY_ID

NOMBRE_MAX = 120

# Valores del ENUM warehouses.type.
TIPOS_ALMACEN = ("general", "scrap", "retreading")

# Activos que se guardan en un almacén: recurso del cuerpo -> tabla.
ACTIVOS_DE_ALMACEN = {"tire_ids": "tires", "sensor_ids": "sensors", "tbox_ids": "tboxes"}

MAX_POR_ASIGNACION = 500


def limpiar_nombre(valor) -> str:
    """Nombre de sede o almacén: sin espacios sobrantes, no vacío, con tope."""
    nombre = " ".join(str(valor or "").split())
    if not nombre:
        raise ValueError("El nombre no puede ir vacío")
    if len(nombre) > NOMBRE_MAX:
        raise ValueError(f"El nombre admite hasta {NOMBRE_MAX} caracteres")
    return nombre


def compania_que_pide(event):
    """El company_id del query string, o None. Lanza ValueError si no es entero."""
    qs = (event or {}).get("queryStringParameters") or {}
    if not qs.get("company_id"):
        return None
    return int(qs["company_id"])


def en_alcance(company_que_pide, company_de_la_fila) -> bool:
    """La admin opera todo; las demás solo lo suyo. Sin company_id no se acota."""
    if company_que_pide is None or company_que_pide == ADMIN_COMPANY_ID:
        return True
    return company_que_pide == company_de_la_fila


def es_duplicado(exc) -> bool:
    """Choque con un índice UNIQUE (nombre repetido en la compañía o la sede)."""
    return isinstance(exc, pymysql.err.IntegrityError) and exc.args and exc.args[0] == 1062


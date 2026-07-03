from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id
from shared.utils.response import error, ok

# Recurso (acepta nombre REST y nombre de tabla) -> tabla real.
TABLES = {
    "vehicles": "units", "units": "units",
    "tires": "tires",
    "sensors": "sensors",
    "tboxes": "tboxes",
}


def _lifecycle(rec: dict) -> str:
    """Estado de sincronización derivado, para que el front sepa qué mostrar/refrescar.

    registering: creado local, aún sin daijin_id (sync pendiente -> 202 del create).
    active:      sincronizado (tiene daijin_id) y vivo.
    deleting:    borrado local pero aún en Dajin (limpieza remota pendiente -> 202 del delete).
    deleted:     borrado en ambos lados.
    """
    if rec.get("is_deleted"):
        return "deleting" if rec.get("daijin_id") else "deleted"
    if not rec.get("daijin_id") or rec.get("status") == "registering":
        return "registering"
    return "active"


def handler(event, context):
    # GET /status/{resource}/{id} -> estado de sincronización del activo.
    params = event.get("pathParameters") or {}
    resource = params.get("resource")
    table = TABLES.get(resource)
    if not table:
        return error(404, f"recurso inválido: {resource} (usa {sorted(set(TABLES))})")
    try:
        rid = int(params["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id inválido")

    db = get_db()
    try:
        rec = get_by_id(db, t(table), rid)
    except Exception as e:
        return error(500, f"DB error (status {resource}): {e}")
    if not rec:
        return error(404, f"{resource} no encontrado")

    return ok({
        "id": rec.get("id"),
        "lifecycle": _lifecycle(rec),
        "status": rec.get("status"),
        "is_deleted": rec.get("is_deleted"),
        "daijin_id": rec.get("daijin_id"),
    })

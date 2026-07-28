from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where
from shared.utils.response import error, ok


def handler(event, context):
    # GET /packages/{id} -> el paquete con sus miembros (tbox + sensores).
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")

    try:
        tboxes = get_where(db, t("tboxes"), "package_id = %s", [pid], 50)
        sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    except Exception as e:
        return error(500, f"DB error (get package members): {e}")

    return ok({
        **pkg,
        "tbox": tboxes[0] if tboxes else None,
        "sensors": sensors,
    })

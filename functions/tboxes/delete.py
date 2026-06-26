from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, soft_delete
from shared.utils.response import error, ok


def handler(event, context):
    # DELETE /tboxes/{id} -> soft delete (marca is_deleted=1, no borra la fila).
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de tbox inválido")

    db = get_db()
    rec = get_by_id(db, t("tboxes"), rid)
    if not rec:
        return error(404, "Tbox no encontrado")
    if rec.get("is_deleted"):
        return ok(rec)

    # TODO Dajin: contrato de borrado sin confirmar (ver nota en vehicles/delete.py).

    try:
        rec = soft_delete(db, t("tboxes"), rid)
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (soft delete tbox): {e}")

from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import exists, get_by_id, soft_delete, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending_delete


def handler(event, context):
    # DELETE /tboxes/{id} -> Dajin-first: borra en Dajin (basic-api) y luego soft-delete local.
    # Nota: el TBox SÍ tiene delete OFICIAL en la OpenAPI (`tbox/delete`). Aquí se usa la
    # basic-api por uniformidad con los otros 3; migrar a la vía oficial cuando se cablee.
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
    # Guard local: no se puede borrar asignado a una unidad; quítalo primero.
    if exists(db, t("units"), {"tbox_id": rid, "is_deleted": 0}):
        return error(409, "El tbox está asignado a una unidad; quítalo primero")

    # Dajin-first: intentar el borrado remoto antes de tocar local.
    daijin_id = rec.get("daijin_id")
    if daijin_id:
        status, msg = attempt_delete("tbox", str(daijin_id))
        if status == GUARD:
            return error(409, "No se pudo completar el borrado")
    else:
        status, msg = DONE, None

    try:
        if status == TRANSIENT:
            rec = soft_delete(db, t("tboxes"), rid)
            db.commit()
            return pending_delete(rec, msg)
        rec = update(db, t("tboxes"), rid, {
            "is_deleted": 1, "daijin_id": None, "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (delete tbox, daijin_id={daijin_id}): {e}")

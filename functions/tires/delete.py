from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, soft_delete, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending_delete


def handler(event, context):
    # DELETE /tires/{id} -> Dajin-first: borra en Dajin (basic-api) y luego soft-delete local.
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    db = get_db()
    rec = get_by_id(db, t("tires"), rid)
    if not rec:
        return error(404, "Llanta no encontrada")
    if rec.get("is_deleted"):
        return ok(rec)
    # Guard local: no se puede borrar montada o con sensor; desvincula ambos primero.
    if rec.get("unit_id") or rec.get("sensor_id"):
        return error(409, "La llanta está montada o tiene sensor; desvincula primero")

    # Dajin-first: intentar el borrado remoto antes de tocar local.
    daijin_id = rec.get("daijin_id")
    if daijin_id:
        status, msg = attempt_delete("tyre", str(daijin_id))
        if status == GUARD:
            return error(409, "No se pudo completar el borrado")
    else:
        status, msg = DONE, None

    try:
        if status == TRANSIENT:
            rec = soft_delete(db, t("tires"), rid)
            db.commit()
            return pending_delete(rec, msg)
        rec = update(db, t("tires"), rid, {
            "is_deleted": 1, "daijin_id": None, "updated_at": now_ms(),
        })
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (delete llanta, daijin_id={daijin_id}): {e}")

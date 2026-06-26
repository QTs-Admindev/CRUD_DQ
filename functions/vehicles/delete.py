from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, soft_delete
from shared.utils.response import error, ok


def handler(event, context):
    # DELETE /vehicles/{id} -> soft delete (marca is_deleted=1, no borra la fila).
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de vehículo inválido")

    db = get_db()
    rec = get_by_id(db, t("units"), rid)
    if not rec:
        return error(404, "Vehículo no encontrado")
    if rec.get("is_deleted"):
        return ok(rec)  # ya estaba borrado -> idempotente

    # TODO Dajin: el contrato de borrado de Dajin NO está confirmado (no existe en el
    # código v1 ni en asset-manager; Dajin maneja isDeleted internamente). Confirmar
    # vía la sección OpenAPI de Dajin / soporte antes de wirear el borrado remoto.

    try:
        rec = soft_delete(db, t("units"), rid)
        db.commit()
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (soft delete vehículo): {e}")

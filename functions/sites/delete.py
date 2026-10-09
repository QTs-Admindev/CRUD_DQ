from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import exists, get_by_id
from shared.locations import compania_que_pide, en_alcance
from shared.utils.response import error, ok


def handler(event, context):
    # DELETE /sites/{id}?company_id=.. -> borra una sede VACÍA. Con unidades o
    # almacenes dentro es 409: primero se mueven, para que nada quede apuntando a
    # una sede que ya no existe.
    try:
        site_id = int((event.get("pathParameters") or {})["id"])
        quien = compania_que_pide(event)
    except (KeyError, TypeError, ValueError):
        return error(400, "id de sede o company_id inválido")

    db = get_db()
    sede = get_by_id(db, t("sites"), site_id)
    if not sede or not en_alcance(quien, sede["company_id"]):
        return error(404, "Sede no encontrada")
    if exists(db, t("units"), {"site_id": site_id, "is_deleted": 0}):
        return error(409, "La sede tiene unidades; muévelas a otra sede antes de borrarla")
    if exists(db, t("warehouses"), {"site_id": site_id}):
        return error(409, "La sede tiene almacenes; bórralos o muévelos antes de borrarla")

    try:
        with db.cursor() as cur:
            # Las unidades borradas (is_deleted=1) no cuentan, pero no deben quedar
            # apuntando a una sede inexistente.
            cur.execute(f"UPDATE {t('units')} SET site_id = NULL WHERE site_id = %s", [site_id])
            cur.execute(f"DELETE FROM {t('sites')} WHERE id = %s", [site_id])
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (borrar sede): {e}")

    audit(db, event, context, action="delete", asset_type="site", asset_id=site_id,
          natural_key=sede["name"], company_id=sede["company_id"], result="success")
    return ok({"id": site_id, "deleted": True})

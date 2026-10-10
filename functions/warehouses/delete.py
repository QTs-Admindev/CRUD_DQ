from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id
from shared.locations import ACTIVOS_DE_ALMACEN, compania_que_pide, en_alcance
from shared.utils.response import error, ok

_NOMBRES = {"tires": "llantas", "sensors": "sensores", "tboxes": "Qbox"}


def handler(event, context):
    # DELETE /warehouses/{id}?company_id=.. -> borra un almacén VACÍO. Con llantas,
    # sensores o Qbox dentro es 409: primero se mueven a otro almacén.
    try:
        wid = int((event.get("pathParameters") or {})["id"])
        quien = compania_que_pide(event)
    except (KeyError, TypeError, ValueError):
        return error(400, "id de almacén o company_id inválido")

    db = get_db()
    alm = get_by_id(db, t("warehouses"), wid)
    if not alm or not en_alcance(quien, alm["company_id"]):
        return error(404, "Almacén no encontrado")

    ocupado = []
    with db.cursor() as cur:
        for tabla in ACTIVOS_DE_ALMACEN.values():
            cur.execute(f"SELECT COUNT(*) FROM {t(tabla)} WHERE warehouse_id = %s "
                        f"AND (is_deleted IS NULL OR is_deleted = 0)", [wid])
            n = int(cur.fetchone()[0])
            if n:
                ocupado.append(f"{n} {_NOMBRES[tabla]}")
    if ocupado:
        return error(409, f"El almacén tiene {', '.join(ocupado)}; muévelos antes de borrarlo")

    try:
        with db.cursor() as cur:
            for tabla in ACTIVOS_DE_ALMACEN.values():
                cur.execute(f"UPDATE {t(tabla)} SET warehouse_id = NULL WHERE warehouse_id = %s",
                            [wid])
            cur.execute(f"DELETE FROM {t('warehouses')} WHERE id = %s", [wid])
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (borrar almacén): {e}")

    audit(db, event, context, action="delete", asset_type="warehouse", asset_id=wid,
          natural_key=alm["name"], company_id=alm["company_id"], result="success")
    return ok({"id": wid, "deleted": True})

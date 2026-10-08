import json

from pydantic import BaseModel, Field, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_in
from shared.locations import MAX_POR_ASIGNACION, compania_que_pide, en_alcance
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class AssignUnitsRequest(BaseModel):
    # None = sacar las unidades de su sede.
    site_id: int | None = None
    unit_ids: list[int] = Field(min_length=1, max_length=MAX_POR_ASIGNACION)


def handler(event, context):
    # POST /sites/assign?company_id=.. -> pone varias unidades en una sede (o las
    # saca, con site_id null). Todo o nada: si una unidad no se puede, no se mueve
    # ninguna y la respuesta dice cuáles y por qué.
    try:
        quien = compania_que_pide(event)
    except ValueError:
        return error(400, "company_id inválido")
    try:
        body = AssignUnitsRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    ids = sorted(set(body.unit_ids))
    db = get_db()

    sede = None
    if body.site_id is not None:
        sede = get_by_id(db, t("sites"), body.site_id)
        if not sede or not en_alcance(quien, sede["company_id"]):
            return error(404, "Sede no encontrada")

    unidades = {u["id"]: u for u in get_in(db, t("units"), "id", ids,
                                           "id, company_id, site_id, is_deleted")}
    no_existen = [i for i in ids if i not in unidades or unidades[i].get("is_deleted")
                  or not en_alcance(quien, unidades[i]["company_id"])]
    if no_existen:
        return error(422, {"message": "Unidades que no existen", "unit_ids": no_existen})
    if sede:
        ajenas = [i for i in ids if unidades[i]["company_id"] != sede["company_id"]]
        if ajenas:
            return error(422, {"message": "Unidades de otra compañía que la sede",
                               "unit_ids": ajenas})

    try:
        marcas = ", ".join(["%s"] * len(ids))
        with db.cursor() as cur:
            cur.execute(f"UPDATE {t('units')} SET site_id = %s, updated_at = %s "
                        f"WHERE id IN ({marcas})", [body.site_id, now_ms(), *ids])
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar sede): {e}")

    audit(db, event, context, action="update", asset_type="unit",
          natural_key=sede["name"] if sede else None,
          company_id=sede["company_id"] if sede else None, result="success",
          payload={"unit_ids": ids},
          changes={"site_id": {str(i): [unidades[i].get("site_id"), body.site_id] for i in ids}})
    return ok({"site_id": body.site_id, "unit_ids": ids})

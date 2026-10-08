import json

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_in
from shared.locations import ACTIVOS_DE_ALMACEN, MAX_POR_ASIGNACION, compania_que_pide, en_alcance
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class AssignStockRequest(BaseModel):
    # None = sacar los activos de su almacén.
    warehouse_id: int | None = None
    tire_ids: list[int] = []
    sensor_ids: list[int] = []
    tbox_ids: list[int] = []


def handler(event, context):
    # POST /warehouses/assign?company_id=.. -> pone llantas, sensores y Qbox en un
    # almacén (o los saca, con warehouse_id null). Todo o nada: si un activo no se
    # puede, no se mueve ninguno y la respuesta dice cuáles y por qué.
    # Estar montado o ligado no impide tener almacén: es a dónde pertenece.
    try:
        quien = compania_que_pide(event)
    except ValueError:
        return error(400, "company_id inválido")
    try:
        body = AssignStockRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    pedidos = {campo: sorted(set(getattr(body, campo))) for campo in ACTIVOS_DE_ALMACEN}
    total = sum(len(v) for v in pedidos.values())
    if total == 0:
        return error(422, "Manda al menos un activo en tire_ids, sensor_ids o tbox_ids")
    if total > MAX_POR_ASIGNACION:
        return error(422, f"Máximo {MAX_POR_ASIGNACION} activos por asignación")

    db = get_db()
    alm = None
    if body.warehouse_id is not None:
        alm = get_by_id(db, t("warehouses"), body.warehouse_id)
        if not alm or not en_alcance(quien, alm["company_id"]):
            return error(404, "Almacén no encontrado")

    previos: dict = {}
    no_existen: dict = {}
    ajenos: dict = {}
    for campo, ids in pedidos.items():
        if not ids:
            continue
        tabla = ACTIVOS_DE_ALMACEN[campo]
        filas = {r["id"]: r for r in get_in(db, t(tabla), "id", ids,
                                            "id, company_id, warehouse_id, is_deleted")}
        malos = [i for i in ids if i not in filas or filas[i].get("is_deleted")
                 or not en_alcance(quien, filas[i]["company_id"])]
        if malos:
            no_existen[campo] = malos
        if alm:
            otros = [i for i in ids if i in filas and i not in malos
                     and filas[i]["company_id"] != alm["company_id"]]
            if otros:
                ajenos[campo] = otros
        previos[tabla] = {i: filas[i].get("warehouse_id") for i in ids if i in filas}
    if no_existen:
        return error(422, {"message": "Activos que no existen", **no_existen})
    if ajenos:
        return error(422, {"message": "Activos de otra compañía que el almacén", **ajenos})

    try:
        ts = now_ms()
        with db.cursor() as cur:
            for campo, ids in pedidos.items():
                if not ids:
                    continue
                marcas = ", ".join(["%s"] * len(ids))
                cur.execute(f"UPDATE {t(ACTIVOS_DE_ALMACEN[campo])} "
                            f"SET warehouse_id = %s, updated_at = %s WHERE id IN ({marcas})",
                            [body.warehouse_id, ts, *ids])
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar almacén): {e}")

    audit(db, event, context, action="update", asset_type="warehouse",
          asset_id=body.warehouse_id, natural_key=alm["name"] if alm else None,
          company_id=alm["company_id"] if alm else None, result="success",
          payload=pedidos,
          changes={tabla: {str(i): [antes, body.warehouse_id] for i, antes in filas.items()}
                   for tabla, filas in previos.items()})
    return ok({"warehouse_id": body.warehouse_id, **pedidos})

import json

import pymysql
from pydantic import BaseModel, ValidationError

from shared.audit import actor_from, audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, insert
from shared.locations import compania_que_pide, en_alcance, es_duplicado
from shared.operadores import DRIVERS, asignaciones, nombre_de, soltar, vigente
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


class AssignDriverRequest(BaseModel):
    # None = dejar la unidad sin operador.
    driver_id: int | None = None
    # El operador ya maneja otra unidad: con move=true se la quita y lo pasa aquí.
    move: bool = False


def handler(event, context):
    # PUT /vehicles/{id}/driver?company_id=.. -> asigna (o quita) el operador de una unidad.
    try:
        rid = int((event.get("pathParameters") or {}).get("id"))
    except (TypeError, ValueError):
        return error(400, "id inválido")
    try:
        quien = compania_que_pide(event)
    except ValueError:
        return error(400, "company_id inválido")
    try:
        body = AssignDriverRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    unidad = get_by_id(db, t("units"), rid)
    if not unidad or unidad.get("is_deleted") or not en_alcance(quien, unidad["company_id"]):
        return error(404, "Unidad no encontrada")
    if unidad.get("deleting_at"):
        return error(409, "La unidad se está borrando")

    actual = vigente(db, "unit_id", rid)
    antes = actual["driver_id"] if actual else None
    actor = actor_from(event)
    cuando = now_ms()

    if body.driver_id is None:
        if not actual:
            return ok({"unit_id": rid, "driver_id": None, "changed": False})
        try:
            soltar(db, "unit_id", rid, cuando, actor)
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (quitar operador): {e}")
        _bitacora(db, event, context, unidad, antes, None)
        return ok({"unit_id": rid, "driver_id": None, "changed": True})

    operador = get_by_id(db, DRIVERS, body.driver_id)
    if not operador or not en_alcance(quien, operador.get("company_id")):
        return error(404, "Operador no encontrado")
    if not operador.get("is_active"):
        return error(422, "El operador está dado de baja")
    if operador.get("company_id") != unidad["company_id"]:
        return error(422, "El operador es de otra compañía que la unidad")
    if antes == body.driver_id:
        return ok(_respuesta(actual, unidad, operador, changed=False))

    otra = vigente(db, "driver_id", body.driver_id)
    previa = (get_by_id(db, t("units"), otra["unit_id"]) or {}) if otra else {}
    if otra and not body.move and not previa.get("is_deleted"):
        return error(409, {"message": "El operador ya maneja otra unidad",
                           "unit_id": otra["unit_id"],
                           "unit_identifier": previa.get("unit_identifier")})

    try:
        soltar(db, "unit_id", rid, cuando, actor)
        if otra:
            soltar(db, "driver_id", body.driver_id, cuando, actor)
        nueva = insert(db, asignaciones(), {
            "company_id": unidad["company_id"], "unit_id": rid,
            "driver_id": body.driver_id, "assigned_at": cuando, "assigned_by": actor,
        })
        db.commit()
    except pymysql.err.IntegrityError as e:
        db.rollback()
        if es_duplicado(e):
            return error(409, "Otra persona cambió esta asignación; recarga e intenta de nuevo")
        return error(500, f"DB error (asignar operador): {e}")
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar operador): {e}")

    _bitacora(db, event, context, unidad, antes, body.driver_id,
              movido_de=otra["unit_id"] if otra else None)
    return ok(_respuesta(nueva, unidad, operador, changed=True))


def _respuesta(fila, unidad, operador, changed):
    return {
        "id": fila.get("id"), "unit_id": unidad["id"],
        "unit_identifier": unidad.get("unit_identifier"),
        "driver_id": operador["id"], "driver_name": nombre_de(operador),
        "assigned_at": fila.get("assigned_at"), "assigned_by": fila.get("assigned_by"),
        "changed": changed,
    }


def _bitacora(db, event, context, unidad, antes, despues, movido_de=None):
    payload = {"moved_from_unit_id": movido_de} if movido_de else None
    audit(db, event, context, action="update", asset_type="unit", asset_id=unidad["id"],
          natural_key=unidad.get("unit_identifier"), company_id=unidad["company_id"],
          daijin_id=unidad.get("daijin_id"), payload=payload,
          changes={"driver_id": [antes, despues]})

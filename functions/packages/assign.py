import json
import os

from pydantic import BaseModel, ValidationError

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending

from functions.packages.layout import tire_slots
# Reusamos EXACTO el alta de llanta y los binds (local-first + sync a Dajin + audit).
# Referencias a nivel módulo para poder mockearlas en tests.
from functions.tires.create import handler as tire_create_handler
from functions.bindings.bind_tire import handler as bind_tire_handler
from functions.bindings.bind_sensor import handler as bind_sensor_handler
from functions.vehicles.bind_tbox import handler as bind_tbox_handler


class AssignPackageRequest(BaseModel):
    unit_id: int


def _record(resp: dict) -> dict:
    data = json.loads(resp["body"])
    return data.get("data", data) if isinstance(data, dict) else data


# Fila centinela del catálogo genérico: la MISMA que el FE usa para "llanta
# genérica" (checkbox Desconocida). Resolverla por convención evita depender de
# un env var y elimina el drift FE<->backend. (brand, model, size, position)
_GENERIC_SENTINEL = ("Desconocida", "DESCONOCIDA", "DESCONOCIDA", "ALL")


def _resolve_generic_catalog_id(db):
    """Id de la fila genérica de tires_catalog.

    1) Si GENERIC_TIRES_CATALOG_ID viene en el entorno, se respeta (override).
    2) Si no, se busca por la convención centinela que ya usa el FE.
    Devuelve int, o None si no existe ninguna.
    """
    override = os.environ.get("GENERIC_TIRES_CATALOG_ID")
    if override:
        return int(override)
    brand, model, size, position = _GENERIC_SENTINEL
    rows = get_where(
        db, "tires_catalog",
        "brand = %s AND model = %s AND size = %s AND position = %s",
        [brand, model, size, position], 1)
    return rows[0]["id"] if rows else None


def handler(event, context):
    # POST /packages/{id}/assign -> monta el paquete en una unidad real: por cada
    # posición del layout reutiliza la llanta ya montada o crea una genérica, ata
    # sensor[i]->llanta[i] y finalmente ata el tbox a la unidad.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")
    try:
        body = AssignPackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") != "prepared":
        return error(409, f"El paquete no se puede asignar (status={pkg.get('status')})")

    unit = get_by_id(db, t("units"), body.unit_id)
    if not unit:
        return error(404, "Unidad no encontrada")
    if unit.get("unit_catalog_id") != pkg.get("unit_catalog_id"):
        return error(422, "El tipo de la unidad no coincide con el del paquete")

    catalog = get_by_id(db, "unit_catalog", pkg.get("unit_catalog_id"))
    if not catalog:
        return error(422, "unit_catalog del paquete no encontrado")
    slots = tire_slots(catalog)

    # Miembros del paquete (orden estable id ASC para mapear sensor[i] -> slot[i]).
    tboxes = get_where(db, t("tboxes"), "package_id = %s", [pid], 1)
    if not tboxes:
        return error(409, "El paquete no tiene tbox")
    tbox = tboxes[0]
    sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    if len(sensors) < len(slots):
        return error(422, f"El paquete tiene {len(sensors)} sensores; el layout requiere {len(slots)}")

    generic_catalog = _resolve_generic_catalog_id(db)
    company_id = unit.get("company_id")
    headers = (event or {}).get("headers") or {}

    for i, slot in enumerate(slots):
        pos = slot["mount_position"]

        # a) Reutilizar la llanta ya montada en esta posición, o crear una genérica.
        live = get_where(
            db, t("tires"),
            "unit_id = %s AND mount_position = %s AND (is_deleted IS NULL OR is_deleted = 0)",
            [body.unit_id, pos], 1)
        if live:
            tire = live[0]
        else:
            if not generic_catalog:
                return error(500, "No hay catálogo genérico: falta la fila centinela "
                                  "(Desconocida/DESCONOCIDA/ALL) en tires_catalog, o define "
                                  "GENERIC_TIRES_CATALOG_ID")
            cresp = tire_create_handler(
                {"body": json.dumps({
                    "prefix": "PKG",
                    "folio": f"PKG{pid}-{pos}",
                    "company_id": company_id,
                    "tires_catalog_id": int(generic_catalog),
                }), "headers": headers}, context)
            if cresp["statusCode"] == 202:
                return pending({"stage": "tire", "mount_position": pos, "reason": _record(cresp)})
            if cresp["statusCode"] != 200:
                return cresp
            tire = _record(cresp)
        tire_id = tire["id"]

        # b) Montar la llanta en la unidad (si no está ya montada).
        if not tire.get("is_mounted"):
            bresp = bind_tire_handler(
                {"pathParameters": {"id": str(body.unit_id)},
                 "body": json.dumps({
                     "tire_id": tire_id,
                     "axle_index": slot["axle_index"],
                     "wheel_index": slot["wheel_index"],
                     "mount_position": pos,
                 }), "headers": headers}, context)
            if bresp["statusCode"] != 200:
                return bresp

        # c) Atar el sensor de esta posición a la llanta (si no tiene ya uno).
        if not tire.get("sensor_id"):
            sresp = bind_sensor_handler(
                {"pathParameters": {"id": str(tire_id)},
                 "body": json.dumps({
                     "sensor_id": sensors[i]["id"],
                     "axle_index": slot["axle_index"],
                     "wheel_index": slot["wheel_index"],
                 }), "headers": headers}, context)
            if sresp["statusCode"] != 200:
                return sresp

    # d) Atar el tbox a la unidad.
    tresp = bind_tbox_handler(
        {"pathParameters": {"id": str(body.unit_id)},
         "body": json.dumps({"tbox_id": tbox["id"]}), "headers": headers}, context)
    if tresp["statusCode"] != 200:
        return tresp

    # e) Marcar el paquete como asignado.
    try:
        update(db, t("packages"), pid, {
            "status": "assigned", "unit_id": body.unit_id, "updated_at": now_ms(),
        })
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar paquete): {e}")

    rec = get_by_id(db, t("packages"), pid)
    audit(db, event, context, action="bind", asset_type="package", asset_id=pid,
          natural_key=pkg.get("name"), company_id=company_id, result="success",
          changes={"unit_id": body.unit_id, "status": "assigned"})
    return ok(rec)

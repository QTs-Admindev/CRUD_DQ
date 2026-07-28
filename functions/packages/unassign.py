import json

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending

# Reusamos EXACTO los unbind y el delete (local-first + sync a la plataforma + audit).
# Referencias a nivel módulo para poder mockearlas en tests.
from functions.vehicles.unbind_tbox import handler as unbind_tbox_handler
from functions.bindings.unbind_sensor import handler as unbind_sensor_handler
from functions.tires.delete import handler as tire_delete_handler


def _record(resp: dict) -> dict:
    data = json.loads(resp["body"])
    return data.get("data", data) if isinstance(data, dict) else data


def handler(event, context):
    # POST /packages/{id}/unassign -> deshace el assign: quita el tbox de la unidad,
    # desvincula los sensores del paquete y ELIMINA solo las llantas genéricas que el
    # propio paquete creó (folio 'PKG{pid}-...'); las llantas reales que la unidad ya
    # tenía se dejan montadas (solo se les quita el sensor del paquete). El paquete
    # vuelve a 'prepared' para poder reasignarse.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") != "assigned":
        return error(409, f"El paquete no está asignado (status={pkg.get('status')})")

    unit_id = pkg.get("unit_id")
    unit = get_by_id(db, t("units"), unit_id) if unit_id else None
    if not unit:
        return error(409, "El paquete está asignado pero la unidad no existe")

    sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    sensor_ids = {s["id"] for s in sensors}
    tbox = (get_where(db, t("tboxes"), "package_id = %s", [pid], 1) or [None])[0]
    like = f"PKG{pid}-%"
    headers = (event or {}).get("headers") or {}

    # 1. Quitar el tbox de la unidad (reverso de la última atadura del assign).
    if tbox and unit.get("tbox_id") == tbox["id"]:
        uresp = unbind_tbox_handler(
            {"pathParameters": {"id": str(unit_id)}, "headers": headers}, context)
        if uresp["statusCode"] != 200:
            return uresp

    # 2. Llantas REALES reutilizadas (folio != PKG{pid}-...): solo se les quita el
    #    sensor del paquete; la llanta se queda montada, era de la unidad.
    reused = get_where(
        db, t("tires"),
        "unit_id = %s AND sensor_id IS NOT NULL AND folio NOT LIKE %s "
        "AND (is_deleted IS NULL OR is_deleted = 0)",
        [unit_id, like], 500)
    for tire in reused:
        if tire.get("sensor_id") in sensor_ids:
            sresp = unbind_sensor_handler(
                {"pathParameters": {"id": str(tire["id"])}, "headers": headers}, context)
            if sresp["statusCode"] != 200:
                return sresp

    # 3. Llantas genéricas que creó el paquete (folio PKG{pid}-...): delete en cascada
    #    (libera el sensor + desmonta + borra). El sensor sobrevive en inventario y
    #    sigue siendo miembro del paquete (conserva su package_id / mount_position).
    pkg_tires = get_where(
        db, t("tires"),
        "unit_id = %s AND folio LIKE %s AND (is_deleted IS NULL OR is_deleted = 0)",
        [unit_id, like], 500)
    for tire in pkg_tires:
        dresp = tire_delete_handler(
            {"pathParameters": {"id": str(tire["id"])}, "headers": headers}, context)
        if dresp["statusCode"] == 202:
            return pending({"stage": "tire_delete", "tire_id": tire["id"], "reason": _record(dresp)})
        if dresp["statusCode"] != 200:
            return dresp

    # 4. El paquete vuelve a estar disponible.
    try:
        update(db, t("packages"), pid, {"status": "prepared", "unit_id": None, "updated_at": now_ms()})
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (des-asignar paquete): {e}")

    rec = get_by_id(db, t("packages"), pid)
    audit(db, event, context, action="unbind", asset_type="package", asset_id=pid,
          natural_key=pkg.get("name"), company_id=pkg.get("company_id"), result="success",
          changes={"status": "prepared", "unit_id": None})
    return ok(rec)

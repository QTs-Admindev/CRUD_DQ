import json
import os

import boto3
from pydantic import BaseModel, ValidationError

from shared.audit import actor_from, audit
from shared.generic_tire import resolve_generic_catalog_id
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


def _resolve_generic_catalog_id(db):
    """Id de la fila genérica de tires_catalog (ver shared/generic_tire)."""
    return resolve_generic_catalog_id(db, get_where)


def _audit_partial(db, event, context, pkg, pid, company_id, stage, pos, done, total, reason=None):
    """Deja rastro (audit result='pending') de un assign que quedó a medias: en qué
    etapa/posición se detuvo y cuántas posiciones ya se montaron. Así el conciliador o
    soporte lo pueden retomar y el paquete no queda a medias en silencio."""
    audit(db, event, context, action="bind", asset_type="package", asset_id=pid,
          natural_key=(pkg or {}).get("name"), company_id=company_id, result="pending",
          payload={"stage": stage, "mount_position": pos, "done": done, "total": total,
                   "reason": reason})


ASSIGNABLE = ("prepared", "assign_failed")


def _load(db, pid, unit_id, statuses):
    """Valida paquete, unidad y layout. Devuelve (pkg, unit, slots) o una respuesta de error."""
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") not in statuses:
        return error(409, f"El paquete no se puede asignar (status={pkg.get('status')})")
    if pkg.get("status") == "assign_failed" and pkg.get("unit_id") not in (None, unit_id):
        return error(409, "El paquete quedó a medias en otra unidad; reintenta en esa unidad")

    unit = get_by_id(db, t("units"), unit_id)
    if not unit:
        return error(404, "Unidad no encontrada")
    if unit.get("unit_catalog_id") != pkg.get("unit_catalog_id"):
        return error(422, "El tipo de la unidad no coincide con el del paquete")

    catalog = get_by_id(db, "unit_catalog", pkg.get("unit_catalog_id"))
    if not catalog:
        return error(422, "unit_catalog del paquete no encontrado")
    return pkg, unit, tire_slots(catalog)


def _claim(db, pid, unit_id, total):
    """Pasa el paquete a 'assigning' solo si sigue asignable. False si otro ya lo tomó."""
    with db.cursor() as cur:
        n = cur.execute(
            f"UPDATE {t('packages')} SET status = 'assigning', unit_id = %s, assign_done = 0, "
            "assign_total = %s, assign_error = NULL, updated_at = %s "
            "WHERE id = %s AND status IN ('prepared', 'assign_failed')",
            [unit_id, total, now_ms(), pid])
    db.commit()
    return n == 1


def _invoke_worker(pid, unit_id, actor):
    fn = os.environ.get("ASSIGN_WORKER_FUNCTION")
    if not fn:
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=fn,
            InvocationType="Event",
            Payload=json.dumps({"package_id": pid, "unit_id": unit_id, "actor": actor}).encode(),
        )
        return True
    except Exception:
        return False


def mark_failed(db, pid, reason):
    try:
        update(db, t("packages"), pid, {
            "status": "assign_failed", "assign_error": str(reason)[:500], "updated_at": now_ms(),
        })
        db.commit()
    except Exception:
        db.rollback()


def start_assign(db, event, pid, unit_id):
    """Valida, marca el paquete como 'assigning' y lanza el worker. La API no espera
    el montaje: con 6 a 10 llantas pasa del límite de 29 s de API Gateway."""
    loaded = _load(db, pid, unit_id, ASSIGNABLE)
    if isinstance(loaded, dict):
        return loaded
    pkg, unit, slots = loaded
    sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    if len(sensors) < len(slots):
        return error(422, f"El paquete tiene {len(sensors)} sensores; el layout requiere {len(slots)}")

    try:
        claimed = _claim(db, pid, unit_id, len(slots))
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (asignar paquete): {e}")
    if not claimed:
        return error(409, "El paquete ya se está asignando")

    if not _invoke_worker(pid, unit_id, actor_from(event)):
        mark_failed(db, pid, "No se pudo iniciar la asignación; reintenta")
    return pending(get_by_id(db, t("packages"), pid))


def handler(event, context):
    # POST /packages/{id}/assign -> 202 de inmediato; el montaje corre en assign_worker.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")
    try:
        body = AssignPackageRequest.model_validate(json.loads(event.get("body") or "{}"))
    except ValidationError as e:
        return error(422, e.errors())
    return start_assign(get_db(), event, pid, body.unit_id)


def assign_now(event, context, on_progress=None):
    # Monta el paquete en una unidad real: por cada
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
    loaded = _load(db, pid, body.unit_id, ASSIGNABLE + ("assigning",))
    if isinstance(loaded, dict):
        return loaded
    pkg, unit, slots = loaded

    # Miembros del paquete.
    # Un paquete puede no tener tbox (lote: solo sensores, para tipos no motrices
    # como remolques donde el Qbox vive en el tractor). En ese caso no se ata tbox.
    tboxes = get_where(db, t("tboxes"), "package_id = %s", [pid], 1)
    tbox = tboxes[0] if tboxes else None
    sensors = get_where(db, t("sensors"), "package_id = %s", [pid], 500)
    if len(sensors) < len(slots):
        return error(422, f"El paquete tiene {len(sensors)} sensores; el layout requiere {len(slots)}")

    # Mapa sensor -> posición por el mount_position que el create selló (determinista,
    # no depende del orden de id). Solo se usa si TODAS las posiciones del layout tienen
    # un sensor; si no (paquetes viejos sin mount_position), se cae al orden id ASC.
    by_position = {s.get("mount_position"): s for s in sensors if s.get("mount_position") is not None}
    use_positions = all(slot["mount_position"] in by_position for slot in slots)

    generic_catalog = _resolve_generic_catalog_id(db)
    company_id = unit.get("company_id")
    headers = (event or {}).get("headers") or {}
    total = len(slots)
    done = 0  # posiciones ya montadas por completo (llanta + sensor)

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
                _audit_partial(db, event, context, pkg, pid, company_id, "tire", pos, done, total,
                               "sin catálogo genérico")
                return error(500, "No hay catálogo genérico: falta la fila centinela "
                                  "(Desconocida/DESCONOCIDA/ALL) en tires_catalog, o define "
                                  "GENERIC_TIRES_CATALOG_ID")
            cresp = tire_create_handler(
                {"body": json.dumps({
                    "prefix": "PKG",
                    "folio": f"PKG{pid}-{pos}",
                    "origin": "unconfirmed",
                    "company_id": company_id,
                    "tires_catalog_id": int(generic_catalog),
                }), "headers": headers}, context)
            if cresp["statusCode"] == 202:
                _audit_partial(db, event, context, pkg, pid, company_id, "tire", pos, done, total,
                               _record(cresp))
                return pending({"stage": "tire", "mount_position": pos,
                                "done": done, "total": total, "reason": _record(cresp)})
            if cresp["statusCode"] != 200:
                _audit_partial(db, event, context, pkg, pid, company_id, "tire", pos, done, total,
                               _record(cresp))
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
                _audit_partial(db, event, context, pkg, pid, company_id, "bind_tire", pos, done, total,
                               _record(bresp))
                return bresp

        # c) Atar el sensor de esta posición a la llanta (si no tiene ya uno).
        if not tire.get("sensor_id"):
            sensor_for_slot = by_position[pos] if use_positions else sensors[i]
            sresp = bind_sensor_handler(
                {"pathParameters": {"id": str(tire_id)},
                 "body": json.dumps({
                     "sensor_id": sensor_for_slot["id"],
                     "axle_index": slot["axle_index"],
                     "wheel_index": slot["wheel_index"],
                 }), "headers": headers}, context)
            if sresp["statusCode"] != 200:
                _audit_partial(db, event, context, pkg, pid, company_id, "bind_sensor", pos, done, total,
                               _record(sresp))
                return sresp

        done += 1  # esta posición quedó completa
        if on_progress:
            on_progress(done, total)

    # d) Atar el tbox a la unidad. Solo si el paquete tiene tbox; un lote (sin tbox)
    #    solo monta llantas + sensores y se salta este paso.
    if tbox:
        tresp = bind_tbox_handler(
            {"pathParameters": {"id": str(body.unit_id)},
             "body": json.dumps({"tbox_id": tbox["id"]}), "headers": headers}, context)
        if tresp["statusCode"] != 200:
            _audit_partial(db, event, context, pkg, pid, company_id, "bind_tbox", None, done, total,
                           _record(tresp))
            return tresp

    # e) Marcar el paquete como asignado. Este es el estado TERMINAL del paquete:
    #    asignar es IRREVERSIBLE, el kit queda consumido y NO existe unassign. Una
    #    vez montado, cualquier cambio posterior se hace a mano, sensor por sensor,
    #    sobre la unidad en la plataforma; el paquete ya no se puede recuperar.
    try:
        update(db, t("packages"), pid, {
            "status": "assigned", "unit_id": body.unit_id, "assign_done": total,
            "assign_error": None, "updated_at": now_ms(),
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

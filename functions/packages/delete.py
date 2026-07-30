from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, update
from shared.utils.clock import now_ms
from shared.utils.response import error, ok


def handler(event, context):
    # DELETE /packages/{id} -> retiro (soft delete) del paquete.
    # `packages` no tiene is_deleted: el borrado suave es status='retired'.
    # No se puede retirar un paquete ya asignado: asignar es TERMINAL e irreversible,
    # el kit quedó consumido y montado en una unidad. No hay unassign que lo recupere.
    try:
        pid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de paquete inválido")

    db = get_db()
    pkg = get_by_id(db, t("packages"), pid)
    if not pkg:
        return error(404, "Paquete no encontrado")
    if pkg.get("status") == "retired":
        return ok(pkg)  # idempotente
    if pkg.get("status") == "assigned":
        return error(409, "El paquete ya fue asignado (consumido) y montado en una unidad; "
                          "no se puede eliminar")

    try:
        ts = now_ms()
        update(db, t("packages"), pid, {"status": "retired", "updated_at": ts})
        # Liberar a los miembros del paquete (quedan como inventario suelto de nuevo).
        for tbox in get_where(db, t("tboxes"), "package_id = %s", [pid], 50):
            update(db, t("tboxes"), tbox["id"], {"package_id": None, "updated_at": ts})
        for sensor in get_where(db, t("sensors"), "package_id = %s", [pid], 500):
            update(db, t("sensors"), sensor["id"], {"package_id": None, "updated_at": ts})
        db.commit()
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (retirar paquete): {e}")

    rec = get_by_id(db, t("packages"), pid)
    audit(db, event, context, action="update", asset_type="package", asset_id=pid,
          natural_key=pkg.get("name"), company_id=pkg.get("company_id"), result="success",
          changes={"status": "retired"})
    return ok(rec)

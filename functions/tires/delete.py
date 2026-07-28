import re

from shared.audit import audit
from shared.config import t
from shared.db.connection import get_db
from shared.db.ops import get_by_id, get_where, soft_delete, update
from shared.smarttyre.basic_api import DONE, GUARD, TRANSIENT, attempt_delete
from shared.smarttyre.client import SmartTyreClient, SmartTyreError
from shared.utils.clock import now_ms
from shared.utils.response import error, ok, pending_delete

from functions.packages.layout import tire_slots

# Folio de una llanta genérica de paquete: PKG{package_id}-{mount_position}.
_PKG_FOLIO = re.compile(r"^PKG(\d+)-(\d+)$")

# Sentinela interna: una llamada a la plataforma falló de forma TRANSITORIA (red/5xx)
# durante la recuperación. El handler responde 502 (reintentable) sin tocar lo local.
_NET_FAIL = "__net__"


def _recover_package_platform(db, rid, daijin_id, m):
    """Recupera el estado de la plataforma desde el paquete y lo limpia.

    Contexto de la divergencia: la llanta ya está LIMPIA en local (desmontada y sin
    sensor), pero en LA PLATAFORMA sigue montada en su vehículo CON su sensor. Por eso
    el borrado remoto rechaza (guard 531 "llanta con sensor") y, como local ya no tiene
    el vehicleId, no hay forma de desvincular el sensor "en frío". La solución es
    RECONSTRUIR el vehículo/sensor/posición desde el paquete que armó esta llanta
    genérica y limpiar la plataforma antes de reintentar el borrado.

    Devuelve (status, msg) del re-intento de borrado remoto, o (_NET_FAIL, msg) si una
    llamada a la plataforma falló de forma transitoria (el handler responde 502).
    """
    pid = int(m.group(1))
    pos = int(m.group(2))

    # Reconstruir el contexto de la plataforma a partir del paquete.
    pkg = get_by_id(db, t("packages"), pid)
    unit = get_by_id(db, t("units"), pkg.get("unit_id")) if pkg and pkg.get("unit_id") else None
    vehicle_id = unit.get("daijin_id") if unit else None

    # El sensor de esta posición (por el mount_position que selló el create del paquete).
    sensors = get_where(db, t("sensors"), "package_id = %s", [pid])
    sensor = next((s for s in sensors if s.get("mount_position") == pos), None)
    sensor_code = sensor.get("sensorCode") if sensor else None

    # axle/wheel de esta posición según el layout del unit_catalog del paquete.
    catalog = get_by_id(db, "unit_catalog", pkg.get("unit_catalog_id")) if pkg else None
    slot = next((s for s in tire_slots(catalog) if s["mount_position"] == pos), None) if catalog else None
    axle = slot["axle_index"] if slot else None
    wheel = slot["wheel_index"] if slot else None

    st = SmartTyreClient()

    # 1) Liberar el sensor en la plataforma. El sensor SOBREVIVE, solo se desvincula.
    #    Un rechazo de negocio (SmartTyreError) significa "ya estaba desvinculado":
    #    ese es justo el estado que buscamos, así que se tolera. Un fallo de red sí
    #    aborta (502, reintentable) para no dejar medio-estado.
    try:
        st.post("/smartyre/openapi/tyre/sensor/unbind", {
            "tyreCode": str(rid),
            "vehicleId": vehicle_id,
            "axleIndex": axle,
            "wheelIndex": wheel,
            "sensorCode": sensor_code,
        })
    except SmartTyreError:
        pass  # ya desvinculado en la plataforma: estado deseado
    except Exception as e:
        return (_NET_FAIL, str(e))

    # 2) Desmontar la llanta en la plataforma. Igual: rechazo de negocio = "ya estaba
    #    desmontada" (se tolera); fallo de red aborta con 502.
    try:
        st.post("/smartyre/openapi/vehicle/tyre/unbind", {
            "vehicleId": vehicle_id,
            "tyreCode": str(rid),
        })
    except SmartTyreError:
        pass  # ya desmontada en la plataforma: estado deseado
    except Exception as e:
        return (_NET_FAIL, str(e))

    # 3) La plataforma quedó limpia: reintentar el borrado remoto.
    return attempt_delete("tyre", str(daijin_id))


def handler(event, context):
    # DELETE /tires/{id} -> cascade so the user never has to unbind manually:
    #   1) free its sensor (stays in inventory, NOT deleted)
    #   2) unmount it from its vehicle
    #   3) delete the tyre remotely (basic-api) + soft-delete local.
    # The remote delete GUARDs a tyre that still has a sensor/vehicle, so the
    # unbinds MUST happen first.
    try:
        rid = int((event.get("pathParameters") or {})["id"])
    except (KeyError, TypeError, ValueError):
        return error(400, "id de llanta inválido")

    db = get_db()
    rec = get_by_id(db, t("tires"), rid)
    if not rec:
        return error(404, "Llanta no encontrada")
    if rec.get("is_deleted"):
        return ok(rec)

    unit = get_by_id(db, t("units"), rec["unit_id"]) if rec.get("unit_id") else None

    # 1) Free the sensor (it survives, in inventory).
    if rec.get("sensor_id"):
        sensor = get_by_id(db, t("sensors"), rec["sensor_id"])
        try:
            st = SmartTyreClient()
            st.post("/smartyre/openapi/tyre/sensor/unbind", {
                "tyreCode": str(rid),
                "vehicleId": unit.get("daijin_id") if unit else None,
                "axleIndex": rec.get("axle_index"),
                "wheelIndex": rec.get("wheel_index"),
                "sensorCode": sensor.get("sensorCode") if sensor else None,
            })
        except Exception as e:
            # Una llanta ALMACENADA puede conservar su sensor (regla de negocio),
            # pero en la plataforma la cadena vehículo-llanta-sensor ya se disolvió
            # al desmontarla: el unbind remoto sin vehicleId no tiene contexto y
            # Dajin lo rechaza. En ese caso el unbind es solo higiene: se libera
            # el sensor localmente y el borrado continúa. Si la llanta sigue
            # MONTADA, el rechazo es real y aborta como antes.
            if rec.get("unit_id"):
                return error(502, "No se pudo liberar el sensor, intenta de nuevo")
            audit(db, event, context, action="unbind", asset_type="sensor",
                  asset_id=rec["sensor_id"],
                  natural_key=sensor.get("sensorCode") if sensor else None,
                  company_id=rec.get("company_id"), result="pending",
                  error=f"unbind remoto rechazado (llanta almacenada): {e}")
        try:
            update(db, t("tires"), rid, {"sensor_id": None, "updated_at": now_ms()})
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (liberar sensor): {e}")

    # 2) Unmount from its vehicle.
    if rec.get("unit_id"):
        if unit and unit.get("daijin_id"):
            try:
                st = SmartTyreClient()
                st.post("/smartyre/openapi/vehicle/tyre/unbind", {
                    "vehicleId": unit.get("daijin_id"),
                    "tyreCode": str(rid),
                })
            except Exception:
                return error(502, "No se pudo desmontar la llanta, intenta de nuevo")
        try:
            update(db, t("tires"), rid, {
                "unit_id": None, "is_mounted": 0, "axle_index": None,
                "wheel_index": None, "mount_position": None, "updated_at": now_ms(),
            })
            db.commit()
        except Exception as e:
            db.rollback()
            return error(500, f"DB error (desmontar llanta): {e}")

    # 3) Delete the tyre. Platform-first (basic-api) then soft-delete local.
    daijin_id = rec.get("daijin_id")
    if daijin_id:
        status, msg = attempt_delete("tyre", str(daijin_id))
        if status == GUARD:
            # La plataforma rechaza el borrado (llanta aún con sensor/vehículo allá,
            # aunque local ya esté limpia). Para una llanta genérica de paquete
            # (folio PKG{pid}-{pos}) reconstruimos el vehículo/sensor desde el paquete,
            # limpiamos la plataforma y reintentamos. Para el resto, se aborta igual.
            m = _PKG_FOLIO.match(rec.get("folio") or "")
            if m:
                status, msg = _recover_package_platform(db, rid, daijin_id, m)
                if status == _NET_FAIL:
                    # Fallo transitorio de la plataforma al limpiar: reintentable.
                    # NO se hace soft-delete -> nunca dejamos medio-estado.
                    return error(502, "No se pudo limpiar la plataforma, intenta de nuevo")
            if status == GUARD:
                # Sigue rechazando (o no es llanta de paquete): NO borrar en local
                # para no quedar en medio-estado (local borrado, plataforma viva).
                return error(409, "No se pudo completar el borrado")
    else:
        status, msg = DONE, None

    try:
        if status == TRANSIENT:
            rec = soft_delete(db, t("tires"), rid)
            db.commit()
            audit(db, event, context, action="update", asset_type="tire", asset_id=rid,
                  natural_key=rec.get("folio"), company_id=rec.get("company_id"),
                  daijin_id=daijin_id, result="pending", changes={"is_deleted": 1}, error=msg)
            return pending_delete(rec, msg)
        rec = update(db, t("tires"), rid, {
            "is_deleted": 1, "daijin_id": None, "updated_at": now_ms(),
        })
        db.commit()
        audit(db, event, context, action="update", asset_type="tire", asset_id=rid,
              natural_key=rec.get("folio"), company_id=rec.get("company_id"),
              daijin_id=daijin_id, result="success", changes={"is_deleted": 1})
        return ok(rec)
    except Exception as e:
        db.rollback()
        return error(500, f"DB error (delete llanta, daijin_id={daijin_id}): {e}")

"""Primera profundidad de una llanta en Loads, para que el FleetView no la muestre vacía.

La carga inicial del FleetView (GPSHook /init/load) toma la última lectura de cada
posición de la unidad. Una llanta recién dada de alta no tiene ninguna hasta que la
plataforma mande la primera, y mientras tanto sale sin mm. Aquí se escribe una sola
vez, con los mm del alta, en cuanto la llanta reúne todo lo que una lectura real
trae: id de la plataforma, sensor, unidad y posición. La primera lectura real queda
más reciente y la reemplaza en pantalla.

No abre alertas: esas solo las abre GPSHook, con lecturas reales.
Best-effort: un fallo aquí nunca tumba el alta, el montaje ni el bind.
"""
import os
from datetime import datetime, timedelta, timezone

from shared.config import DAJIN_ORG_ID, t
from shared.db.ops import get_by_id
from shared.generic_tire import is_generic_catalog

ORIGEN = "alta"
_cliente = None


def _loads():
    """La colección Loads, o None si la función no tiene Mongo (MONGO_SECRET vacío)."""
    global _cliente
    secreto = os.environ.get("MONGO_SECRET")
    if not secreto:
        return None
    if _cliente is None:
        from pymongo import MongoClient
        from shared.secrets.manager import get_secret_json
        uri = get_secret_json(secreto)["MONGO_URI"]
        # Tiempos cortos: el alta no puede esperar a un Mongo lento.
        _cliente = MongoClient(uri, serverSelectionTimeoutMS=3000,
                               connectTimeoutMS=3000, socketTimeoutMS=5000)
    return _cliente[os.environ.get("MONGO_DB", "Quinta")]["Loads"]


def hora_china(ahora: datetime | None = None) -> str:
    """calculateTime como lo guarda GPSHook: hora de China, sin zona."""
    ahora = ahora or datetime.now(timezone.utc)
    return (ahora.astimezone(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def posicion_real(catalogo_unidad: dict | None, eje, rueda) -> int | None:
    """Misma numeración que GPSHook (calculate_real_position): corrida por eje.

    Las de repuesto (eje 0) quedan fuera: GPSHook tampoco les pone posición.
    """
    if not catalogo_unidad or not eje or not rueda:
        return None
    ejes = int(catalogo_unidad.get("axles_count") or 0)
    llantas = [int(catalogo_unidad.get(f"tires_axle_{i}") or 0) for i in range(1, ejes + 1)]
    if not 1 <= eje <= len(llantas) or not 1 <= rueda <= llantas[eje - 1]:
        return None
    return sum(llantas[:eje - 1]) + rueda


def documento(llanta: dict, unidad: dict, catalogo_unidad: dict | None,
              empresa: dict | None, sensor: dict | None, ahora: datetime | None = None) -> dict | None:
    """El documento de Loads, o None si a la llanta le falta algo para salir en pantalla."""
    profundidad = llanta.get("current_depth")
    real = posicion_real(catalogo_unidad, llanta.get("axle_index"), llanta.get("wheel_index"))
    if not (profundidad and float(profundidad) > 0 and real and llanta.get("daijin_id")
            and unidad.get("daijin_id")):
        return None
    hora = hora_china(ahora)
    placa = str(unidad["id"])
    doc = {
        "_id": f"{ORIGEN}-{llanta['id']}",
        "origen": ORIGEN,
        "tyreCode": str(llanta["id"]),
        "tyreId": int(llanta["daijin_id"]),
        "vehicleId": int(unidad["daijin_id"]),
        "orgId": int(DAJIN_ORG_ID),
        "licensePlateNumber": placa,
        "tractorName": placa,
        "axleIndex": int(llanta["axle_index"]),
        "wheelIndex": int(llanta["wheel_index"]),
        "realPosition": real,
        "nowThreadDepth": float(profundidad),
        "calculateTime": hora,
        "receiveTime": hora,
        "unitStatus": unidad.get("status"),
        "unitIdentifier": unidad.get("unit_identifier"),
        "unitType": unidad.get("unit_catalog_id"),
        "company_id": llanta.get("company_id"),
    }
    if empresa:
        doc["company_name"] = empresa.get("company_name")
    tag = "-".join(str(v).strip() for v in (llanta.get("prefix"), llanta.get("folio"))
                   if v is not None and str(v).strip())
    if tag:
        doc["tag"] = tag
    if sensor and sensor.get("sensorCode"):
        doc["sensorCode"] = sensor["sensorCode"]
    return doc


def sembrar(db, tire_id) -> bool:
    """Escribe la profundidad del alta si la llanta ya la puede mostrar. Una sola vez."""
    try:
        loads = _loads()
        if loads is None:
            return False
        llanta = get_by_id(db, t("tires"), tire_id)
        if not llanta or llanta.get("is_deleted") or not llanta.get("sensor_id") \
                or not llanta.get("unit_id"):
            return False
        if is_generic_catalog(get_by_id(db, "tires_catalog", llanta.get("tires_catalog_id"))):
            return False
        unidad = get_by_id(db, t("units"), llanta["unit_id"])
        if not unidad or unidad.get("is_deleted"):
            return False
        doc = documento(
            llanta, unidad,
            get_by_id(db, "unit_catalog", unidad.get("unit_catalog_id")),
            get_by_id(db, "companies", llanta.get("company_id")),
            get_by_id(db, t("sensors"), llanta["sensor_id"]),
        )
        if not doc:
            return False
        # $setOnInsert por _id: reintentos y nuevos montajes no la repiten ni la mueven.
        loads.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
        return True
    except Exception as e:
        print(f"[lectura_alta] no se pudo escribir la profundidad de la llanta {tire_id}: {e}")
        return False

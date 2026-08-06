"""Confirmar que una escritura en la plataforma REALMENTE surtió efecto (read-back).

Cada cambio de binding (asignar/quitar un Qbox, un sensor o una llanta) se manda con un
POST *fire-and-forget* que devuelve 200 aunque no haya cambiado nada: el activo es un
fantasma (ya no existe en la plataforma), la escritura fue un no-op, o la plataforma tarda
en propagar. Grabar un éxito local con solo ese 200 es EXACTAMENTE como divergen los dos
sistemas (la unidad queda con Qbox local pero suelta en la plataforma).

Estas funciones RELEEN el registro autoritativo de la plataforma por su llave natural y
confirman el estado final buscado, con reintentos acotados (consistencia eventual: una
lectura inmediata tras el POST puede ir atrasada). Devuelven un bool; el handler graba
éxito SOLO cuando es True — si no, reporta `pending` para que el barrido de reconciliación
lo reintente. Nunca un falso éxito.

Llaves naturales (mismas que usa el resto del sync):
  vehículo -> licensePlateNumber == id local de la unidad
  llanta   -> tyreCode           == id local de la llanta
  sensor   -> sensorCode         == MAC del sensor
"""
import time

# Espera (s) entre relecturas. El primer intento es inmediato (0.0) porque muchos binds
# propagan al instante; los siguientes cubren la consistencia eventual sin colgar la Lambda.
DEFAULT_BACKOFF = (0.0, 0.5, 1.2, 2.5)

VEHICLE_LIST = "/smartyre/openapi/vehicle/list"
TYRE_LIST = "/smartyre/openapi/tyre/list"
SENSOR_LIST = "/smartyre/openapi/sensor/list"


def _first(st, list_path, list_filter):
    """Primer registro que la plataforma devuelve para esa llave natural, o None."""
    resp = st.get(list_path, list_filter) or {}
    records = resp.get("records") or []
    return records[0] if records else None


def confirm(check, backoff=DEFAULT_BACKOFF):
    """Corre `check()` a lo largo del backoff; True apenas se cumpla, si no False.

    Un error transitorio de lectura NO cuenta como confirmación: se ignora y se
    reintenta. Si nunca se cumple, devuelve False (el handler reporta pending).
    """
    for wait in backoff:
        if wait:
            time.sleep(wait)
        try:
            if check():
                return True
        except Exception:
            pass
    return False


# ----------------------------------------------------------------------------- Qbox <-> vehículo
def tbox_bound(st, *, plate, tbox_code, backoff=DEFAULT_BACKOFF):
    """El vehículo (por placa) trae ese Qbox montado en la plataforma."""
    def check():
        v = _first(st, VEHICLE_LIST, {"licensePlateNumber": str(plate)})
        return bool(v) and str(v.get("tboxCode") or "") == str(tbox_code)
    return confirm(check, backoff)


def tbox_unbound(st, *, plate, backoff=DEFAULT_BACKOFF):
    """El vehículo (por placa) NO trae ningún Qbox en la plataforma."""
    def check():
        v = _first(st, VEHICLE_LIST, {"licensePlateNumber": str(plate)})
        if not v:
            return False  # no podemos confirmar el estado si el vehículo no aparece
        return not v.get("tboxCode") and not v.get("tboxId")
    return confirm(check, backoff)


# ----------------------------------------------------------------------------- sensor <-> llanta
def sensor_on_tyre(st, *, sensor_code, tyre_code, backoff=DEFAULT_BACKOFF):
    """El sensor (por sensorCode) está asociado a esa llanta en la plataforma."""
    def check():
        s = _first(st, SENSOR_LIST, {"sensorCode": str(sensor_code)})
        return bool(s) and str(s.get("tyreCode") or "") == str(tyre_code)
    return confirm(check, backoff)


def sensor_off_tyre(st, *, sensor_code, tyre_code=None, backoff=DEFAULT_BACKOFF):
    """El sensor ya NO está en esa llanta (o desapareció por completo)."""
    def check():
        s = _first(st, SENSOR_LIST, {"sensorCode": str(sensor_code)})
        if not s:
            return True  # el sensor ya no existe -> ciertamente no está en la llanta
        if tyre_code is None:
            return not s.get("tyreCode")
        return str(s.get("tyreCode") or "") != str(tyre_code)
    return confirm(check, backoff)


# ----------------------------------------------------------------------------- llanta <-> vehículo
def tyre_on_vehicle(st, *, tyre_code, plate, backoff=DEFAULT_BACKOFF):
    """La llanta (por tyreCode) está montada en ese vehículo en la plataforma."""
    def check():
        ty = _first(st, TYRE_LIST, {"tyreCode": str(tyre_code)})
        return bool(ty) and str(ty.get("licensePlateNumber") or "") == str(plate)
    return confirm(check, backoff)


def tyre_off_vehicle(st, *, tyre_code, plate=None, backoff=DEFAULT_BACKOFF):
    """La llanta ya NO está en ese vehículo (o desapareció por completo)."""
    def check():
        ty = _first(st, TYRE_LIST, {"tyreCode": str(tyre_code)})
        if not ty:
            return True
        if plate is None:
            return not ty.get("licensePlateNumber")
        return str(ty.get("licensePlateNumber") or "") != str(plate)
    return confirm(check, backoff)

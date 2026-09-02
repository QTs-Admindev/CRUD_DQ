"""Activación verificada contra la plataforma.

`status = 'active'` debe significar "el activo está dado de alta en la plataforma y su
`daijin_id` es el que tenemos guardado", no "alguien lo marcó activo desde el panel".

Antes el PUT escribía el campo tal cual. Una fila sin `daijin_id` podía quedar en
`active`, y con eso se salía de las DOS vías de recuperación (`POST /{recurso}/resync`
y el cron `reconcile`), que buscaban por `status = 'registering'`: quedaba un activo
local que la plataforma no conoce y que en el panel se ve sano.

Aquí vive la confirmación que hace el PUT antes de activar: relee el activo en la
plataforma por su llave natural y devuelve el id autoritativo. El handler activa SOLO
con ese id, y de paso lo guarda si localmente faltaba o no coincidía (autocuración,
mismo criterio que `heal_on_resume`).

Llaves naturales (las mismas que usa el resto del sync):
  unidad -> licensePlateNumber == id local · llanta -> tyreCode == id local
  sensor -> sensorCode (hardware)          · Qbox   -> tboxCode (hardware)
"""
import time

from shared.smarttyre.client import SmartTyreClient
from shared.smarttyre.sync import find_id


class NotOnPlatform(Exception):
    """La plataforma respondió bien y el activo NO está dado de alta ahí."""


class PlatformUnavailable(Exception):
    """No se pudo consultar la plataforma, así que no se puede afirmar nada.

    Distinto de `NotOnPlatform` a propósito: una lectura fallida no es prueba de
    ausencia, y activar por no poder preguntar es exactamente lo que se quiere evitar.
    """


# recurso local -> (endpoint de listado, cómo armar su llave natural desde la fila)
LOOKUP = {
    "units": ("/smartyre/openapi/vehicle/list",
              lambda rec: {"licensePlateNumber": str(rec.get("id"))}),
    "tires": ("/smartyre/openapi/tyre/list",
              lambda rec: {"tyreCode": str(rec.get("id"))}),
    "sensors": ("/smartyre/openapi/sensor/list",
                lambda rec: {"sensorCode": rec.get("sensorCode")}),
    "tboxes": ("/smartyre/openapi/tbox/list",
               lambda rec: {"tboxCode": rec.get("tboxCode")}),
}

# La plataforma es de consistencia eventual: un alta recién hecha puede tardar en
# aparecer en el listado. El primer intento es inmediato.
DEFAULT_BACKOFF = (0.0, 0.6, 1.5)


def confirm_on_platform(rec, resource, *, client=None, backoff=DEFAULT_BACKOFF):
    """Devuelve el `daijin_id` autoritativo del activo en la plataforma.

    rec: la fila local (de ella salen la llave natural y el id).
    resource: "units" | "tires" | "sensors" | "tboxes".

    Lanza `NotOnPlatform` si la plataforma contesta y el activo no aparece, y
    `PlatformUnavailable` si no se pudo consultar (auth, red, error del proveedor).
    """
    try:
        list_path, key_of = LOOKUP[resource]
    except KeyError:
        raise ValueError(f"recurso sin llave natural definida: {resource}")

    try:
        st = client or SmartTyreClient()
    except Exception as e:
        raise PlatformUnavailable(str(e)) from e

    list_filter = key_of(rec)
    read_error = None
    for wait in backoff:
        if wait:
            time.sleep(wait)
        try:
            found = find_id(st, list_path, list_filter)
        except Exception as e:
            # Error de lectura: se reintenta. Si nunca se logra leer, el resultado es
            # "no se pudo confirmar", nunca "no existe".
            read_error = e
            continue
        if found is not None:
            return str(found)

    if read_error is not None:
        raise PlatformUnavailable(str(read_error))
    raise NotOnPlatform(f"{resource}: {list_filter}")

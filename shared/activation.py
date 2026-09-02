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


class NotOnPlatform(Exception):
    """La plataforma respondió bien y el activo NO está dado de alta ahí."""


class PlatformUnavailable(Exception):
    """No se pudo consultar la plataforma, así que no se puede afirmar nada.

    Distinto de `NotOnPlatform` a propósito: una lectura fallida no es prueba de
    ausencia, y activar por no poder preguntar es exactamente lo que se quiere evitar.
    """


# recurso local -> (endpoint de listado, campo de la llave en la plataforma, de dónde
# sale su valor en la fila local)
LOOKUP = {
    "units": ("/smartyre/openapi/vehicle/list", "licensePlateNumber",
              lambda rec: rec.get("id")),
    "tires": ("/smartyre/openapi/tyre/list", "tyreCode",
              lambda rec: rec.get("id")),
    "sensors": ("/smartyre/openapi/sensor/list", "sensorCode",
                lambda rec: rec.get("sensorCode")),
    "tboxes": ("/smartyre/openapi/tbox/list", "tboxCode",
               lambda rec: rec.get("tboxCode")),
}

# La plataforma es de consistencia eventual: un alta recién hecha puede tardar en
# aparecer en el listado. El primer intento es inmediato.
# Dos lecturas, no más. Cada una puede tardar hasta el timeout del cliente (20 s) y la
# Lambda muere a los 30 s, así que un backoff largo no da "502 limpio" sino un corte de
# API Gateway sin cuerpo, que es peor para quien lo recibe.
DEFAULT_BACKOFF = (0.0, 0.8)


def confirm_on_platform(rec, resource, *, client=None, backoff=DEFAULT_BACKOFF):
    """Devuelve el `daijin_id` autoritativo del activo en la plataforma.

    rec: la fila local (de ella salen la llave natural y el id).
    resource: "units" | "tires" | "sensors" | "tboxes".

    Lanza `NotOnPlatform` si la plataforma contesta y el activo no aparece, y
    `PlatformUnavailable` si no se pudo consultar (auth, red, error del proveedor).
    """
    try:
        list_path, key_field, value_of = LOOKUP[resource]
    except KeyError:
        raise ValueError(f"recurso sin llave natural definida: {resource}")

    key_value = value_of(rec)
    # Sin llave natural no hay nada que preguntar: un filtro vacío devuelve el primer
    # registro de la plataforma, y con él se activaría el activo con un id ajeno. Se
    # trata como "no está": el efecto correcto es no activar.
    if key_value is None or str(key_value).strip() == "":
        raise NotOnPlatform(f"{resource}: fila sin llave natural ({key_field})")

    key_value = str(key_value)
    list_filter = {key_field: key_value}

    try:
        st = client or SmartTyreClient()
    except Exception as e:
        raise PlatformUnavailable(str(e)) from e

    read_error = None
    for wait in backoff:
        if wait:
            time.sleep(wait)
        try:
            found = _matching_id(st, list_path, list_filter, key_field, key_value)
        except Exception as e:
            # Error de lectura: se reintenta. Si nunca se logra leer, el resultado es
            # "no se pudo confirmar", nunca "no existe".
            read_error = e
            continue
        if found is not None:
            return found

    if read_error is not None:
        raise PlatformUnavailable(str(read_error))
    raise NotOnPlatform(f"{resource}: {list_filter}")


def _matching_id(st, list_path, list_filter, key_field, key_value):
    """Id del registro cuya llave natural COINCIDE con la que se pidió, o None.

    No basta con tomar el primero del listado: si la plataforma ignorara el filtro
    (parámetro no soportado, versión distinta), el primer registro sería un activo
    cualquiera y se guardaría su id como si fuera el nuestro. Se compara el campo.
    """
    resp = st.get(list_path, list_filter) or {}
    for record in (resp.get("records") or []):
        if str(record.get(key_field) or "") != key_value:
            continue
        found = record.get("id")
        # Un id vacío o 0 no es una confirmación: guardarlo sacaría la fila del barrido
        # de reconciliación para siempre.
        if found in (None, "", 0):
            return None
        return str(found)
    return None

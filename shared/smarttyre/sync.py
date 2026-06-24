"""Sincronización idempotente con SmartTyre/Dajin.

Patrón GET-antes-de-POST: si el activo ya existe en Dajin (por su natural key),
se devuelve su id sin recrearlo — esto MATA los duplicados de raíz. Si no existe,
se crea y se resuelve el id con reintentos acotados, porque Dajin no devuelve el id
en el POST y puede tardar en aparecer en el listado (consistencia eventual).

El id devuelto es el `daijin_id` que se guarda como columna en el activo.
"""
import time

# Espera (segundos) entre reintentos de resolución tras el POST.
DEFAULT_BACKOFF = (0.3, 0.8, 1.5, 3.0)


class SmartTyreNotResolved(Exception):
    """Se intentó crear el activo pero su id no apareció en Dajin a tiempo.

    No implica que la creación haya fallado: pudo crearse y aún no propagar.
    El activo queda en `registering` y el barrido de reconciliación lo retoma.
    """


def _find_id(st, list_path, list_filter):
    resp = st.get(list_path, list_filter) or {}
    records = resp.get("records") or []
    return records[0]["id"] if records else None


def resolve_or_create(st, *, list_path, list_filter, insert_path, insert_payload,
                      assume_new=False, backoff=DEFAULT_BACKOFF):
    """Devuelve el daijin_id del activo, creándolo en Dajin solo si no existe.

    st: cliente SmartTyre (expone .get(path, params) y .post(path, body)).
    list_filter: natural key para localizar el activo (ej. {"sensorCode": "..."}).
    assume_new: si True, se salta el GET previo de idempotencia (más rápido). Solo
        es seguro cuando la llave en Dajin es un id que NOSOTROS acabamos de generar
        (tyreCode/licensePlate = id local), que no puede preexistir. Para llaves de
        hardware externas (sensorCode/tboxCode) debe quedar en False, porque el
        activo podría ya existir en Dajin de forma independiente.
    """
    # 1. Idempotencia: ¿ya existe en Dajin? -> recuperar, no recrear.
    if not assume_new:
        existing = _find_id(st, list_path, list_filter)
        if existing is not None:
            return existing

    # 2. No existe (o asumimos nuevo) -> crear.
    st.post(insert_path, insert_payload)

    # 3. Resolver el id (Dajin no lo devuelve en el POST). Reintentos acotados.
    found = _find_id(st, list_path, list_filter)
    if found is not None:
        return found
    for wait in backoff:
        time.sleep(wait)
        found = _find_id(st, list_path, list_filter)
        if found is not None:
            return found

    raise SmartTyreNotResolved(list_filter)

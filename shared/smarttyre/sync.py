"""Sincronización idempotente con SmartTyre/la plataforma.

Patrón GET-antes-de-POST: si el activo ya existe en la plataforma (por su natural key),
se devuelve su id sin recrearlo — esto MATA los duplicados de raíz. Si no existe,
se crea y se resuelve el id con reintentos acotados, porque la plataforma no devuelve el id
en el POST y puede tardar en aparecer en el listado (consistencia eventual).

El id devuelto es el `daijin_id` que se guarda como columna en el activo.
"""
import time

# Espera (segundos) entre reintentos de resolución tras el POST.
DEFAULT_BACKOFF = (0.3, 0.8, 1.5, 3.0)


class SmartTyreNotResolved(Exception):
    """Se intentó crear el activo pero su id no apareció en la plataforma a tiempo.

    No implica que la creación haya fallado: pudo crearse y aún no propagar.
    El activo queda en `registering` y el barrido de reconciliación lo retoma.
    """


def _find_id(st, list_path, list_filter):
    resp = st.get(list_path, list_filter) or {}
    records = resp.get("records") or []
    return records[0]["id"] if records else None


def resolve_or_create(st, *, list_path, list_filter, insert_path, insert_payload,
                      assume_new=False, backoff=DEFAULT_BACKOFF):
    """Devuelve el daijin_id del activo, creándolo en la plataforma solo si no existe.

    st: cliente SmartTyre (expone .get(path, params) y .post(path, body)).
    list_filter: natural key para localizar el activo (ej. {"sensorCode": "..."}).
    assume_new: si True, se salta el GET previo de idempotencia (más rápido). Solo
        es seguro cuando la llave en la plataforma es un id que NOSOTROS acabamos de generar
        (tyreCode/licensePlate = id local), que no puede preexistir. Para llaves de
        hardware externas (sensorCode/tboxCode) debe quedar en False, porque el
        activo podría ya existir en la plataforma de forma independiente.
    """
    # 1. Idempotencia: ¿ya existe en la plataforma? -> recuperar, no recrear.
    if not assume_new:
        existing = _find_id(st, list_path, list_filter)
        if existing is not None:
            return existing

    # 2. No existe (o asumimos nuevo) -> crear.
    st.post(insert_path, insert_payload)

    # 3. Resolver el id (la plataforma no lo devuelve en el POST). Reintentos acotados.
    found = _find_id(st, list_path, list_filter)
    if found is not None:
        return found
    for wait in backoff:
        time.sleep(wait)
        found = _find_id(st, list_path, list_filter)
        if found is not None:
            return found

    raise SmartTyreNotResolved(list_filter)


def resolve_or_heal(st, *, stored_id, list_path, list_filter, insert_path,
                    insert_payload, backoff=DEFAULT_BACKOFF):
    """Self-heal a record that ALREADY claims a `stored_id`.

    A create can hit an existing local row that is `active` with a `daijin_id`.
    Historically we returned it as-is — but that id can be a PHANTOM: the row was
    marked synced, yet the asset no longer exists in the platform (deleted upstream, or
    the sync half-finished). This verifies against the platform by the natural key and,
    if it truly doesn't resolve, re-creates it, so creation always leaves BOTH
    systems consistent.

    A single empty list read is NOT treated as proof of deletion: the platform is
    eventually consistent, and a transient empty/filtered/rate-limited-but-200
    response looks identical to a real delete. We retry the read across the
    backoff before concluding the asset is gone, and the re-create always does a
    confirming GET-before-POST (never `assume_new`) — in the heal path the asset
    is *expected* to exist, so this prevents re-creating a duplicate.

    Returns (daijin_id, changed): `changed` is True when the authoritative id
    differs from `stored_id` (caller should persist it + audit as a reconcile).
    """
    found = _find_id(st, list_path, list_filter)
    if found is None:
        for wait in backoff:
            time.sleep(wait)
            found = _find_id(st, list_path, list_filter)
            if found is not None:
                break
    if found is None:
        # Confirmed absent across retries -> re-create with GET-before-POST.
        found = resolve_or_create(
            st, list_path=list_path, list_filter=list_filter,
            insert_path=insert_path, insert_payload=insert_payload,
            assume_new=False, backoff=backoff,
        )
    return found, str(found) != str(stored_id)

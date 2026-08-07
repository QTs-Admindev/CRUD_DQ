"""Lock de aplicación por-activo (MySQL GET_LOCK) para serializar operaciones concurrentes
sobre el MISMO activo.

Un bind/unbind hace read-guard -> POST a la plataforma -> verify -> commit sin exclusión
mutua; dos requests sobre la misma unidad/llanta pueden interponerse (p.ej. un bind y un
unbind pisándose -> lost update). Tomar un lock nombrado por-activo alrededor de todo el
handler los serializa. GET_LOCK es por-conexión y sobrevive al commit, así que cubre toda
la sección crítica; cada contenedor Lambda tiene su propia conexión, de modo que el lock
serializa entre contenedores.

Es BEST-EFFORT: si el driver no soporta cursor (dobles de test) NO rompe — corre sin lock.
En producción (PyMySQL) siempre se adquiere.
"""
import contextlib
import functools


@contextlib.contextmanager
def asset_lock(db, key, timeout=10):
    """Adquiere GET_LOCK(key) sobre `db` durante el bloque; lo libera al salir."""
    cur = None
    try:
        cur = db.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (str(key), timeout))
        cur.fetchone()
    except Exception:
        cur = None  # best-effort: sin cursor (tests) -> seguir sin lock
    try:
        yield
    finally:
        if cur is not None:
            try:
                cur.execute("SELECT RELEASE_LOCK(%s)", (str(key),))
                cur.fetchone()
            except Exception:
                pass


def with_asset_lock(key_fn, timeout=10):
    """Decora un handler Lambda para correr bajo el lock del activo que `key_fn(event)` nombre.

    Usa el `get_db` del propio módulo del handler (el mismo que los tests monkeypatchean), así
    el lock va sobre la misma conexión que usa el handler.
    """
    def deco(handler):
        @functools.wraps(handler)
        def wrapped(event, context):
            get_db = handler.__globals__.get("get_db")
            key = None
            try:
                key = key_fn(event)
            except Exception:
                key = None
            if get_db is None or key is None:
                return handler(event, context)
            with asset_lock(get_db(), key, timeout):
                return handler(event, context)
        return wrapped
    return deco

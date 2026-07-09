"""Cliente de la API INTERNA del portal Dajin (`basic-api`).

La OpenAPI oficial (ver `client.py`) NO expone delete para vehículo/llanta/sensor;
la plataforma web sí, vía `basic-api`, autenticada con un **JWT de sesión** que se
obtiene con usuario/contraseña (no con la firma OAuth). Este módulo se usa SOLO para
complementar esos borrados. Cuando Dajin exponga el delete oficial en la OpenAPI,
migrar a `client.py` y retirar este archivo.

Auth: login por password -> JWT (~24h) cacheado en RAM por contenedor, con re-login
automático (single-flight) al vencer o ante un 401. El token NUNCA se loguea.
"""
import threading
import time

import httpx

from shared.secrets.manager import get_secret_json

# El JWT vive solo en memoria del contenedor Lambda (efímero). Lo durable (usuario/
# password) está en Secrets Manager (`DPortalCredentials`).
_token: str | None = None
_token_expires_at: float = 0.0
_login_lock = threading.Lock()

_VERIFY_SSL = False           # dajintruck.com tiene el SSL roto (igual que client.py)
# Timeouts acotados: un connect corto hace que un portal inalcanzable falle rápido
# (antes 20s colgaban el connect). El path síncrono debe volver muy por debajo del
# límite del API Gateway (~29s) para no reventar en el navegador con ERR_FAILED.
_TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0)
_TOKEN_TTL = 23 * 3600        # el JWT dura ~24h; refrescamos con margen

# Una vez consumidos estos segundos, no se inicia un reintento más (se deja pending y
# la reconciliación lo retoma). Mantiene el Lambda síncrono bajo el límite del gateway.
_STOP_RETRIES_AFTER = 6.0

# Resultados de un intento de borrado remoto.
DONE = "done"                 # Dajin confirmó el borrado
GUARD = "guard"               # rechazo de negocio (permanente): no reintentar
TRANSIENT = "transient"       # fallo transitorio (red/5xx/login): reintentable

DEFAULT_BACKOFF = (0.5, 1.5, 3.0)

# Códigos/mensajes de Dajin que significan "el activo ya no existe": el borrado es
# idempotente, así que lo tratamos como ÉXITO (no como rechazo). Visto: al borrar un
# vehículo ya borrado devuelve {"code":900,"msg":"不存在该车辆"} ("no existe").
_ALREADY_GONE_CODES = {900}
_ALREADY_GONE_MARK = "不存在"  # "no existe" (aparece en el msg de todos los recursos)


class BasicApiGuard(Exception):
    """Dajin rechazó el borrado por una regla de negocio (ej. 'llanta con sensor').

    Permanente: hay que resolver la causa (desvincular) antes de reintentar.
    """

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg}")


class BasicApiTransient(Exception):
    """Fallo transitorio (red, 5xx, login, token expirado). Reintentable."""


def _reset_token() -> None:
    global _token, _token_expires_at
    _token = None
    _token_expires_at = 0.0


class BasicApiClient:
    def __init__(self):
        creds = get_secret_json("DPortalCredentials")
        self._base_url = creds["BASE_URL"].rstrip("/")
        self._username = creds["USERNAME"]
        self._password = creds["PASSWORD"]

    def _login(self) -> str:
        global _token, _token_expires_at
        if _token and time.time() < _token_expires_at:
            return _token
        # single-flight: un solo login a la vez aunque lleguen requests en paralelo.
        with _login_lock:
            if _token and time.time() < _token_expires_at:  # re-chequeo dentro del lock
                return _token
            try:
                resp = httpx.post(
                    f"{self._base_url}/user/login",
                    json={"username": self._username, "password": self._password},
                    verify=_VERIFY_SSL, timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json().get("data") or {}
            except (httpx.HTTPError, ValueError) as e:
                raise BasicApiTransient(f"login basic-api falló: {e}")
            token = data.get("token")
            if not token:
                raise BasicApiTransient("login basic-api sin token")
            _token = token
            _token_expires_at = time.time() + _TOKEN_TTL
            return _token

    def delete(self, resource: str, daijin_id) -> bool:
        """DELETE /{resource}/delete/{daijin_id}. resource ∈ vehicle|tyre|sensor|tbox.

        Devuelve True si Dajin confirma el borrado (code 200).
        Lanza BasicApiGuard (rechazo permanente) o BasicApiTransient (reintentable).
        """
        token = self._login()
        try:
            resp = httpx.request(
                "DELETE", f"{self._base_url}/{resource}/delete/{daijin_id}",
                headers={"token": token, "systemtype": "web",
                         "Content-Type": "application/json"},
                verify=_VERIFY_SSL, timeout=_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise BasicApiTransient(f"delete {resource}/{daijin_id}: {e}")

        if resp.status_code == 401:
            _reset_token()  # token muerto -> el reintento re-loguea
            raise BasicApiTransient("token expirado (HTTP 401)")
        if resp.status_code >= 500:
            raise BasicApiTransient(f"delete {resource}/{daijin_id}: HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise BasicApiTransient(f"delete {resource}/{daijin_id}: respuesta no-JSON")

        code = body.get("code")
        msg = body.get("msg") or ""
        if code == 200:
            return True
        if code == 401:
            _reset_token()
            raise BasicApiTransient("token expirado (body code 401)")
        # "ya no existe" (borrado previo) -> idempotente, lo tratamos como éxito.
        if code in _ALREADY_GONE_CODES or _ALREADY_GONE_MARK in msg:
            return True
        # cualquier otro código = rechazo de negocio (guard, ej. 531 llanta con sensor)
        raise BasicApiGuard(code, msg)


def attempt_delete(resource: str, daijin_id, backoff=DEFAULT_BACKOFF):
    """Borra en Dajin con reintentos ante fallos transitorios.

    Devuelve una tupla (status, msg):
      (DONE, None)        Dajin confirmó el borrado.
      (GUARD, msg)        rechazo permanente (no se reintenta; resolver la causa).
      (TRANSIENT, msg)    no se pudo tras los reintentos (dejar pending -> reconciliación).
    """
    try:
        client = BasicApiClient()
    except Exception as e:  # sin credenciales / Secrets Manager caído -> tratar como transitorio
        return (TRANSIENT, f"basic-api no disponible: {e}")

    last = ""
    start = time.monotonic()
    for i in range(len(backoff) + 1):
        # Don't start another attempt once the budget is spent: leave it pending
        # (reconciliation retries) instead of blowing the API Gateway timeout.
        if i > 0 and time.monotonic() - start >= _STOP_RETRIES_AFTER:
            break
        try:
            client.delete(resource, daijin_id)
            return (DONE, None)
        except BasicApiGuard as g:
            return (GUARD, g.msg)
        except BasicApiTransient as tr:
            last = str(tr)
            if i < len(backoff):
                time.sleep(backoff[i])
    return (TRANSIENT, last)

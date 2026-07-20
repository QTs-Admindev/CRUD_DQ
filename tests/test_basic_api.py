import httpx

from shared.smarttyre import basic_api
from shared.smarttyre.basic_api import (
    DONE, GUARD, TRANSIENT, BasicApiClient, attempt_delete,
)


class FakeResp:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeHttpx:
    HTTPError = httpx.HTTPError

    def __init__(self, delete_resp=None, delete_exc=None,
                 login_resp=None, login_exc=None):
        self.delete_resp = delete_resp
        self.delete_exc = delete_exc
        self.login_resp = login_resp or FakeResp(200, {"data": {"token": "tok"}})
        self.login_exc = login_exc
        self.login_calls = 0
        self.delete_calls = 0

    def post(self, url, **kw):                 # el login
        self.login_calls += 1
        if self.login_exc:
            raise self.login_exc
        return self.login_resp

    def request(self, method, url, **kw):      # el DELETE
        self.delete_calls += 1
        if self.delete_exc:
            raise self.delete_exc
        return self.delete_resp


def _wire(monkeypatch, fake, secret_exc=None):
    def get_secret(name):
        if secret_exc:
            raise secret_exc
        return {"BASE_URL": "http://x/basic-api", "USERNAME": "u", "PASSWORD": "p"}
    monkeypatch.setattr(basic_api, "get_secret_json", get_secret)
    monkeypatch.setattr(basic_api, "httpx", fake)
    basic_api._reset_token()
    return fake


# ---------- outcomes básicos ----------

def test_delete_success(monkeypatch):
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 200, "msg": "Success"})))
    assert attempt_delete("vehicle", "1", backoff=()) == (DONE, None)


def test_delete_guard(monkeypatch):
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 531, "msg": "llanta con sensor"})))
    status, msg = attempt_delete("tyre", "1", backoff=())
    assert status == GUARD and msg == "llanta con sensor"


def test_delete_transient_on_5xx(monkeypatch):
    _wire(monkeypatch, FakeHttpx(FakeResp(500)))
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT


def test_delete_transient_on_network_error(monkeypatch):
    _wire(monkeypatch, FakeHttpx(delete_exc=httpx.ConnectError("down")))
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT


# ---------- expiración / reset de token ----------

def test_delete_http_401_is_transient_and_resets_token(monkeypatch):
    _wire(monkeypatch, FakeHttpx(FakeResp(401)))
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT
    assert basic_api._token is None            # token limpiado para re-login


def test_delete_body_code_401_also_resets_token(monkeypatch):
    # Dajin puede devolver HTTP 200 con {"code":401} en el body.
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 401, "msg": "expired"})))
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT
    assert basic_api._token is None


def test_401_then_relogin_then_success(monkeypatch):
    # 401 (token muerto) -> reset -> reintento re-loguea y termina en DONE.
    seq = [FakeResp(401), FakeResp(200, {"code": 200})]

    class Seq(FakeHttpx):
        def request(self, method, url, **kw):
            self.delete_calls += 1
            return seq.pop(0)

    fake = _wire(monkeypatch, Seq())
    assert attempt_delete("vehicle", "1", backoff=(0,))[0] == DONE
    assert fake.login_calls == 2               # login inicial + re-login tras el 401


# ---------- fallos de login ----------

def test_login_network_error_is_transient(monkeypatch):
    _wire(monkeypatch, FakeHttpx(login_exc=httpx.ConnectError("down")))
    status, msg = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT and "login" in msg


def test_login_http_error_is_transient(monkeypatch):
    _wire(monkeypatch, FakeHttpx(login_resp=FakeResp(500)))
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT


def test_login_without_token_is_transient(monkeypatch):
    # login 200 pero sin data.token (p.ej. credenciales malas devueltas como 200/msg).
    _wire(monkeypatch, FakeHttpx(login_resp=FakeResp(200, {"code": 500, "msg": "bad pass"})))
    status, msg = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT and "sin token" in msg


def test_login_non_json_is_transient(monkeypatch):
    _wire(monkeypatch, FakeHttpx(login_resp=FakeResp(200, None)))   # .json() lanza
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT


# ---------- secrets manager caído / sin secreto ----------

def test_missing_secret_degrades_to_transient(monkeypatch):
    _wire(monkeypatch, FakeHttpx(), secret_exc=RuntimeError("secret not found"))
    status, msg = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT and "no disponible" in msg


# ---------- respuestas malformadas ----------

def test_delete_non_json_response_is_transient(monkeypatch):
    _wire(monkeypatch, FakeHttpx(FakeResp(200, None)))              # .json() lanza
    status, _ = attempt_delete("vehicle", "1", backoff=())
    assert status == TRANSIENT


def test_http_200_with_unknown_code_is_guard(monkeypatch):
    # Cualquier code de negocio distinto de 200/401/ya-borrado se trata como rechazo.
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 500, "msg": "internal"})))
    status, msg = attempt_delete("vehicle", "1", backoff=())
    assert status == GUARD and msg == "internal"


def test_code_900_already_gone_is_success(monkeypatch):
    # Borrar algo ya borrado en Dajin -> {"code":900,"msg":"不存在该车辆"} = idempotente OK.
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 900, "msg": "不存在该车辆"})))
    assert attempt_delete("vehicle", "1", backoff=()) == (DONE, None)


def test_not_exist_message_is_success(monkeypatch):
    # Aunque el code fuera otro, el mensaje "不存在" (no existe) también = éxito.
    _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 404, "msg": "不存在该传感器"})))
    assert attempt_delete("sensor", "1", backoff=()) == (DONE, None)


# ---------- reintentos ----------

def test_retries_then_succeeds(monkeypatch):
    seq = [FakeResp(500), FakeResp(200, {"code": 200})]

    class Flaky(FakeHttpx):
        def request(self, method, url, **kw):
            self.delete_calls += 1
            return seq.pop(0)

    fake = _wire(monkeypatch, Flaky())
    assert attempt_delete("vehicle", "1", backoff=(0,))[0] == DONE
    assert fake.delete_calls == 2


def test_guard_stops_retries_immediately(monkeypatch):
    fake = _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 531, "msg": "bound"})))
    status, _ = attempt_delete("tyre", "1", backoff=(0, 0, 0))
    assert status == GUARD
    assert fake.delete_calls == 1              # sin reintentos: el guard es permanente


def test_exhausted_retries_return_last_error(monkeypatch):
    fake = _wire(monkeypatch, FakeHttpx(FakeResp(503)))
    status, msg = attempt_delete("vehicle", "1", backoff=(0, 0))
    assert status == TRANSIENT and "503" in msg
    assert fake.delete_calls == 3              # intento inicial + 2 reintentos


# ---------- cache del token ----------

def test_token_cached_between_calls(monkeypatch):
    fake = _wire(monkeypatch, FakeHttpx(FakeResp(200, {"code": 200})))
    client = BasicApiClient()
    client.delete("vehicle", "1")
    client.delete("vehicle", "2")
    assert fake.login_calls == 1               # un solo login para ambas llamadas


def test_token_never_in_error_messages(monkeypatch):
    # El token no debe filtrarse en los mensajes que terminan en logs/respuestas.
    _wire(monkeypatch, FakeHttpx(FakeResp(500)))
    status, msg = attempt_delete("vehicle", "1", backoff=())
    assert "tok" not in (msg or "")

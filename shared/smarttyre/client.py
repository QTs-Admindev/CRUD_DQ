import json
import secrets
import time

import httpx

from shared.secrets.manager import get_secret_json
from shared.smarttyre.sign import compute_sign

_token: str | None = None
_token_expires_at: float = 0

# dajintruck.com tiene un certificado SSL roto; el sistema legacy también va con
# verify=False. Necesario para conectar.
_VERIFY_SSL = False
_TIMEOUT = 20


class SmartTyreError(Exception):
    """Business-level rejection from Dajin (HTTP 200 but code != 200).

    Dajin wraps every response in {"code": 200, "msg": "Success", "data": ...}.
    A non-200 code means the operation was refused even though the HTTP call
    succeeded (e.g. an invalid bind, or an asset that does not exist upstream).
    Without this, such failures were silently swallowed and only the local DB
    was written.
    """


def _unwrap(resp, path: str):
    """Return the payload's `data`, raising SmartTyreError on a business error."""
    payload = resp.json()
    code = payload.get("code")
    if code is not None and code != 200:
        msg = payload.get("msg") or payload.get("message") or "sin mensaje"
        raise SmartTyreError(f"{path} -> code {code}: {msg}")
    return payload.get("data")


class SmartTyreClient:
    def __init__(self):
        creds = get_secret_json("DCredentials")
        self._base_url = creds["BASE_URL"]
        self._client_id = creds["CLIENT_ID"]
        self._client_secret = creds["CLIENT_SECRET"]
        self._sign_key = creds["SIGN_KEY"]
        self._access_token = self._get_token()

    def _headers(self, body_str: str = "", params: dict | None = None,
                 with_token: bool = True) -> dict:
        base = {
            "clientId": self._client_id,
            "nonce": secrets.token_hex(16),
            "timestamp": str(int(time.time() * 1000)),
        }
        if with_token:
            base["accessToken"] = self._access_token
        sign = compute_sign(base, body_str, params, self._sign_key)
        return {
            **base,
            "sign": sign,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_token(self) -> str:
        global _token, _token_expires_at
        if _token and time.time() < _token_expires_at:
            return _token
        # El authorize TAMBIÉN va firmado (sin accessToken) y con el body como string.
        body_str = json.dumps(
            {
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
                "grantType": "client_credentials",
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        headers = self._headers(body_str=body_str, with_token=False)
        resp = httpx.post(
            f"{self._base_url}/smartyre/openapi/auth/oauth20/authorize",
            content=body_str, headers=headers, verify=_VERIFY_SSL, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        _token = data.get("accessToken")
        if not _token:
            raise RuntimeError(f"Dajin auth sin accessToken: {resp.text[:200]}")
        _token_expires_at = time.time() + 3500
        return _token

    def post(self, path: str, data: dict):
        # Dajin wraps the response in {"code":200,"msg":"Success","data":...}.
        # _unwrap validates the business code (not just HTTP) and returns .data.
        body_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        headers = self._headers(body_str=body_str)
        resp = httpx.post(
            f"{self._base_url}{path}", content=body_str, headers=headers,
            verify=_VERIFY_SSL, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return _unwrap(resp, path)

    def get(self, path: str, params: dict | None = None):
        headers = self._headers(params=params)
        resp = httpx.get(
            f"{self._base_url}{path}", params=params, headers=headers,
            verify=_VERIFY_SSL, timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return _unwrap(resp, path)

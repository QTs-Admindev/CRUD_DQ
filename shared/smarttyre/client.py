import json
import secrets
import time

import httpx

from shared.secrets.manager import get_secret
from shared.smarttyre.sign import compute_sign

_token: str | None = None
_token_expires_at: float = 0


class SmartTyreClient:
    def __init__(self):
        self._base_url = get_secret("SMARTTYRE_BASE_URL")
        self._client_id = get_secret("SMARTTYRE_CLIENT_ID")
        self._client_secret = get_secret("SMARTTYRE_CLIENT_SECRET")
        self._sign_key = get_secret("SMARTTYRE_SIGN_KEY")
        self._access_token = self._get_token()

    def _get_token(self) -> str:
        global _token, _token_expires_at
        if _token and time.time() < _token_expires_at:
            return _token
        resp = httpx.post(
            f"{self._base_url}/smartyre/openapi/auth/oauth20/authorize",
            json={
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
                "grantType": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data["accessToken"]
        _token_expires_at = time.time() + 3500
        return _token

    def _build_headers(self, body_str: str | None = None, params: dict | None = None) -> dict:
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        base_headers = {
            "accessToken": self._access_token,
            "clientId": self._client_id,
            "nonce": nonce,
            "timestamp": ts,
        }
        sign = compute_sign(base_headers, body_str, params, self._sign_key)
        return {**base_headers, "sign": sign, "Content-Type": "application/json"}

    def post(self, path: str, data: dict):
        body_str = json.dumps(data)
        headers = self._build_headers(body_str=body_str)
        resp = httpx.post(f"{self._base_url}{path}", content=body_str, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get(self, path: str, params: dict | None = None):
        headers = self._build_headers(params=params)
        resp = httpx.get(f"{self._base_url}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

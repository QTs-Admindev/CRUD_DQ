import hashlib


def compute_sign(headers: dict, body: str | None, params: dict | None, sign_key: str) -> str:
    raw = ""
    for k in sorted(headers.keys()):
        raw += f"{k}={headers[k]}&"
    if body:
        raw += body + "&"
    if params:
        for k in sorted(params.keys()):
            raw += f"{k}={params[k]}&"
    raw += sign_key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

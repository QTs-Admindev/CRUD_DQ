import hashlib

from shared.smarttyre.sign import compute_sign


def test_sign_headers_only():
    headers = {"clientId": "abc", "timestamp": "1000", "nonce": "xyz", "accessToken": "tok"}
    sign = compute_sign(headers, None, None, "SECRET")
    raw = "accessToken=tok&clientId=abc&nonce=xyz&timestamp=1000&SECRET"
    expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
    assert sign == expected


def test_sign_with_body():
    headers = {"clientId": "abc", "timestamp": "1000", "nonce": "xyz", "accessToken": "tok"}
    body = '{"key":"val"}'
    sign = compute_sign(headers, body, None, "SECRET")
    raw = f'accessToken=tok&clientId=abc&nonce=xyz&timestamp=1000&{body}&SECRET'
    expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
    assert sign == expected


def test_sign_with_params():
    headers = {"clientId": "abc", "timestamp": "1000", "nonce": "xyz", "accessToken": "tok"}
    params = {"tyreCode": "TIRE001"}
    sign = compute_sign(headers, None, params, "SECRET")
    raw = "accessToken=tok&clientId=abc&nonce=xyz&timestamp=1000&tyreCode=TIRE001&SECRET"
    expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
    assert sign == expected

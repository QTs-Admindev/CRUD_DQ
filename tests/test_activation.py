"""Tests de `shared/activation.py`: confirmar contra la plataforma antes de activar.

La distinción que importa: "la plataforma contestó y el activo NO está" (NotOnPlatform,
que el handler traduce a 409) NO es lo mismo que "no se pudo preguntar"
(PlatformUnavailable -> 502). Una lectura fallida nunca es prueba de ausencia.
"""
import pytest

from shared import activation


class FakeClient:
    """Cliente de la plataforma con respuestas programadas por llamada."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params))
        resp = self.responses.pop(0) if self.responses else {"records": []}
        if isinstance(resp, Exception):
            raise resp
        return resp


NO_WAIT = (0.0, 0.0)


def test_returns_platform_id_for_sensor():
    client = FakeClient([{"records": [{"id": 77}]}])
    got = activation.confirm_on_platform(
        {"id": 1, "sensorCode": "AABBCCDDEEFF"}, "sensors", client=client, backoff=NO_WAIT)
    assert got == "77"
    assert client.calls[0] == ("/smartyre/openapi/sensor/list",
                               {"sensorCode": "AABBCCDDEEFF"})


def test_tbox_uses_its_own_natural_key():
    client = FakeClient([{"records": [{"id": 5}]}])
    activation.confirm_on_platform(
        {"id": 2, "tboxCode": "112233445566"}, "tboxes", client=client, backoff=NO_WAIT)
    assert client.calls[0] == ("/smartyre/openapi/tbox/list", {"tboxCode": "112233445566"})


def test_tire_and_unit_use_the_local_id_as_key():
    client = FakeClient([{"records": [{"id": 9}]}])
    activation.confirm_on_platform({"id": 41}, "tires", client=client, backoff=NO_WAIT)
    assert client.calls[0] == ("/smartyre/openapi/tyre/list", {"tyreCode": "41"})

    client = FakeClient([{"records": [{"id": 9}]}])
    activation.confirm_on_platform({"id": 41}, "units", client=client, backoff=NO_WAIT)
    assert client.calls[0] == ("/smartyre/openapi/vehicle/list",
                               {"licensePlateNumber": "41"})


def test_empty_listing_is_not_on_platform():
    client = FakeClient([{"records": []}, {"records": []}])
    with pytest.raises(activation.NotOnPlatform):
        activation.confirm_on_platform(
            {"id": 1, "sensorCode": "AA"}, "sensors", client=client, backoff=NO_WAIT)


def test_read_failure_is_unavailable_not_absence():
    # Que la lectura truene NO prueba que el activo no exista: 502, nunca 409.
    client = FakeClient([RuntimeError("timeout"), RuntimeError("timeout")])
    with pytest.raises(activation.PlatformUnavailable):
        activation.confirm_on_platform(
            {"id": 1, "sensorCode": "AA"}, "sensors", client=client, backoff=NO_WAIT)


def test_retries_cover_eventual_consistency():
    # Primera lectura vacía (aún no propaga), segunda con el registro.
    client = FakeClient([{"records": []}, {"records": [{"id": 12}]}])
    got = activation.confirm_on_platform(
        {"id": 1, "sensorCode": "AA"}, "sensors", client=client, backoff=NO_WAIT)
    assert got == "12"


def test_auth_failure_building_the_client_is_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("auth failed")
    monkeypatch.setattr(activation, "SmartTyreClient", boom)
    with pytest.raises(activation.PlatformUnavailable):
        activation.confirm_on_platform({"id": 1, "sensorCode": "AA"}, "sensors",
                                       backoff=NO_WAIT)

"""Tests de `shared/activation.py`: confirmar contra la plataforma antes de activar.

Dos distinciones que importan:
  - "la plataforma contestó y el activo NO está" (NotOnPlatform -> 409 en el handler) no
    es lo mismo que "no se pudo preguntar" (PlatformUnavailable -> 502). Una lectura
    fallida nunca es prueba de ausencia.
  - el registro que devuelve el listado tiene que ser EL que se pidió. Si la plataforma
    ignorara el filtro, el primer registro sería un activo cualquiera y se guardaría su
    id como si fuera el nuestro.
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

SENSOR = {"id": 1, "sensorCode": "AABBCCDDEEFF"}
TBOX = {"id": 2, "tboxCode": "112233445566"}


def test_returns_platform_id_for_sensor():
    client = FakeClient([{"records": [{"id": 77, "sensorCode": "AABBCCDDEEFF"}]}])
    got = activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)
    assert got == "77"
    assert client.calls[0] == ("/smartyre/openapi/sensor/list",
                               {"sensorCode": "AABBCCDDEEFF"})


def test_tbox_uses_its_own_natural_key():
    client = FakeClient([{"records": [{"id": 5, "tboxCode": "112233445566"}]}])
    got = activation.confirm_on_platform(TBOX, "tboxes", client=client, backoff=NO_WAIT)
    assert got == "5"
    assert client.calls[0] == ("/smartyre/openapi/tbox/list", {"tboxCode": "112233445566"})


def test_tire_and_unit_use_the_local_id_as_key():
    client = FakeClient([{"records": [{"id": 9, "tyreCode": "41"}]}])
    assert activation.confirm_on_platform(
        {"id": 41}, "tires", client=client, backoff=NO_WAIT) == "9"
    assert client.calls[0] == ("/smartyre/openapi/tyre/list", {"tyreCode": "41"})

    client = FakeClient([{"records": [{"id": 9, "licensePlateNumber": "41"}]}])
    assert activation.confirm_on_platform(
        {"id": 41}, "units", client=client, backoff=NO_WAIT) == "9"
    assert client.calls[0] == ("/smartyre/openapi/vehicle/list",
                               {"licensePlateNumber": "41"})


def test_empty_listing_is_not_on_platform():
    client = FakeClient([{"records": []}, {"records": []}])
    with pytest.raises(activation.NotOnPlatform):
        activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)


def test_read_failure_is_unavailable_not_absence():
    # Que la lectura truene NO prueba que el activo no exista: 502, nunca 409.
    client = FakeClient([RuntimeError("timeout"), RuntimeError("timeout")])
    with pytest.raises(activation.PlatformUnavailable):
        activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)


def test_retries_cover_eventual_consistency():
    # Primera lectura vacía (aún no propaga), segunda con el registro.
    client = FakeClient([{"records": []},
                         {"records": [{"id": 12, "sensorCode": "AABBCCDDEEFF"}]}])
    got = activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)
    assert got == "12"


def test_auth_failure_building_the_client_is_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("auth failed")
    monkeypatch.setattr(activation, "SmartTyreClient", boom)
    with pytest.raises(activation.PlatformUnavailable):
        activation.confirm_on_platform(SENSOR, "sensors", backoff=NO_WAIT)


# ---------------- el registro devuelto tiene que ser el que se pidió ----------------

def test_a_record_that_does_not_match_the_key_is_not_a_confirmation():
    # La plataforma ignora el filtro y devuelve otro sensor: NO es nuestro activo.
    client = FakeClient([{"records": [{"id": 999, "sensorCode": "OTROSENSOR11"}]},
                         {"records": [{"id": 999, "sensorCode": "OTROSENSOR11"}]}])
    with pytest.raises(activation.NotOnPlatform):
        activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)


def test_picks_the_matching_record_when_the_listing_returns_several():
    client = FakeClient([{"records": [
        {"id": 999, "sensorCode": "OTROSENSOR11"},
        {"id": 77, "sensorCode": "AABBCCDDEEFF"},
    ]}])
    assert activation.confirm_on_platform(
        SENSOR, "sensors", client=client, backoff=NO_WAIT) == "77"


def test_an_empty_or_zero_platform_id_is_not_a_confirmation():
    # Guardar un id vacío sacaría la fila del barrido de reconciliación para siempre.
    client = FakeClient([{"records": [{"id": 0, "sensorCode": "AABBCCDDEEFF"}]},
                         {"records": [{"id": None, "sensorCode": "AABBCCDDEEFF"}]}])
    with pytest.raises(activation.NotOnPlatform):
        activation.confirm_on_platform(SENSOR, "sensors", client=client, backoff=NO_WAIT)


# ------------------------------ llave natural ausente ------------------------------

@pytest.mark.parametrize("rec, resource", [
    ({"id": 1, "sensorCode": None}, "sensors"),
    ({"id": 1, "sensorCode": "   "}, "sensors"),
    ({"id": 2, "tboxCode": None}, "tboxes"),
    ({"id": None}, "tires"),
])
def test_a_row_without_natural_key_never_reaches_the_platform(rec, resource):
    # Un filtro vacío devolvería el primer registro de la plataforma y se activaría el
    # activo con un id ajeno. Ni siquiera se pregunta.
    client = FakeClient([{"records": [{"id": 999, "sensorCode": "CUALQUIERA12"}]}])
    with pytest.raises(activation.NotOnPlatform):
        activation.confirm_on_platform(rec, resource, client=client, backoff=NO_WAIT)
    assert client.calls == []

"""Primitiva de verificación por read-back (shared/smarttyre/verify.py).

Este archivo queda EXCLUIDO del fixture autouse que confirma binding (ver conftest),
para ejercitar las funciones reales contra un st.get falso.
"""
from shared.smarttyre import verify

NB = (0.0,)  # sin espera entre relecturas (tests rápidos)


class St:
    """Fake del cliente: devuelve records fijos por list_path."""
    def __init__(self, by_path):
        self.by_path = by_path

    def get(self, path, params=None):
        return {"records": list(self.by_path.get(path, []))}


# --------------------------------------------------------------- Qbox <-> vehículo
def test_tbox_bound_true():
    st = St({verify.VEHICLE_LIST: [{"tboxCode": "AA11", "tboxId": 5}]})
    assert verify.tbox_bound(st, plate=1903, tbox_code="AA11", backoff=NB) is True


def test_tbox_bound_false_when_phantom_vehicle_missing():
    assert verify.tbox_bound(St({}), plate=1903, tbox_code="AA11", backoff=NB) is False


def test_tbox_bound_false_when_wrong_code():
    st = St({verify.VEHICLE_LIST: [{"tboxCode": "BBBB"}]})
    assert verify.tbox_bound(st, plate=1903, tbox_code="AA11", backoff=NB) is False


def test_tbox_unbound_true_when_no_code():
    st = St({verify.VEHICLE_LIST: [{"tboxCode": None, "tboxId": None}]})
    assert verify.tbox_unbound(st, plate=1903, backoff=NB) is True


def test_tbox_unbound_false_when_still_bound():
    st = St({verify.VEHICLE_LIST: [{"tboxCode": "AA11"}]})
    assert verify.tbox_unbound(st, plate=1903, backoff=NB) is False


def test_tbox_unbound_false_when_vehicle_not_visible():
    # No se puede AFIRMAR que quedó libre si el vehículo no aparece.
    assert verify.tbox_unbound(St({}), plate=1903, backoff=NB) is False


# --------------------------------------------------------------- sensor <-> llanta
def test_sensor_on_tyre_true_false():
    assert verify.sensor_on_tyre(St({verify.SENSOR_LIST: [{"tyreCode": "30357"}]}),
                                 sensor_code="A4", tyre_code="30357", backoff=NB) is True
    assert verify.sensor_on_tyre(St({verify.SENSOR_LIST: [{"tyreCode": "999"}]}),
                                 sensor_code="A4", tyre_code="30357", backoff=NB) is False
    assert verify.sensor_on_tyre(St({}),
                                 sensor_code="A4", tyre_code="30357", backoff=NB) is False


def test_sensor_off_tyre():
    # sensor desaparecido -> ciertamente fuera
    assert verify.sensor_off_tyre(St({}), sensor_code="A4", tyre_code="30357", backoff=NB) is True
    # sensor en OTRA llanta -> fuera de esta
    assert verify.sensor_off_tyre(St({verify.SENSOR_LIST: [{"tyreCode": "999"}]}),
                                  sensor_code="A4", tyre_code="30357", backoff=NB) is True
    # sensor sigue en la misma llanta -> NO está fuera
    assert verify.sensor_off_tyre(St({verify.SENSOR_LIST: [{"tyreCode": "30357"}]}),
                                  sensor_code="A4", tyre_code="30357", backoff=NB) is False


# --------------------------------------------------------------- llanta <-> vehículo
def test_tyre_on_vehicle_true_false():
    assert verify.tyre_on_vehicle(St({verify.TYRE_LIST: [{"licensePlateNumber": "1903"}]}),
                                  tyre_code="10", plate=1903, backoff=NB) is True
    assert verify.tyre_on_vehicle(St({verify.TYRE_LIST: [{"licensePlateNumber": "1"}]}),
                                  tyre_code="10", plate=1903, backoff=NB) is False


def test_tyre_off_vehicle():
    assert verify.tyre_off_vehicle(St({}), tyre_code="10", plate=1903, backoff=NB) is True
    assert verify.tyre_off_vehicle(St({verify.TYRE_LIST: [{"licensePlateNumber": "1903"}]}),
                                   tyre_code="10", plate=1903, backoff=NB) is False


# --------------------------------------------------------------- semántica de confirm()
def test_confirm_retries_across_backoff():
    # 1a lectura vacía (aún no propaga), 2a ya refleja el bind.
    seq = iter([[], [{"tboxCode": "AA11"}]])

    class St2:
        def get(self, path, params=None):
            return {"records": next(seq)}

    assert verify.tbox_bound(St2(), plate=1, tbox_code="AA11", backoff=(0.0, 0.0)) is True


def test_confirm_read_error_is_not_a_confirmation():
    class StErr:
        def get(self, path, params=None):
            raise RuntimeError("lectura transitoria falló")

    # Un error de lectura NUNCA cuenta como confirmación (fail-closed) -> False.
    assert verify.tbox_bound(StErr(), plate=1, tbox_code="AA11", backoff=(0.0,)) is False

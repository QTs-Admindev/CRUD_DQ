import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import shared.smarttyre.verify as _verify

# Las funciones de verificación por read-back (bind/unbind confirmados en la plataforma).
_VERIFY_FNS = (
    "tbox_bound", "tbox_unbound",
    "sensor_on_tyre", "sensor_off_tyre",
    "tyre_on_vehicle", "tyre_off_vehicle",
)


@pytest.fixture(autouse=True)
def _confirm_platform(request, monkeypatch):
    """Por defecto, la plataforma CONFIRMA cada binding (read-back = True).

    Así los tests de handlers exestentes siguen viendo 200 sin cambios. Un test que quiera
    probar el camino 'la plataforma NO confirmó -> 202 pending' hace
    `monkeypatch.setattr(verify, "<fn>", lambda *a, **k: False)` dentro del test (se aplica
    después de este fixture y gana). `test_verify.py` queda excluido para ejercitar la
    primitiva real contra un st.get falso.
    """
    if request.module.__name__.rsplit(".", 1)[-1] == "test_verify":
        return
    for name in _VERIFY_FNS:
        monkeypatch.setattr(_verify, name, lambda *a, **k: True)

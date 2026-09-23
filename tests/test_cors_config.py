"""El CORS de las rutas tiene que aceptar X-Actor, o el navegador bloquea las
peticiones del FE que la mandan (y la bitácora vuelve a quedar como 'system')."""
import os

import yaml

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _serverless():
    with open(os.path.join(_RAIZ, "serverless.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_el_cors_comun_acepta_x_actor():
    headers = _serverless()["custom"]["cors"]["headers"]
    assert "X-Actor" in headers
    assert "Content-Type" in headers


def test_todas_las_rutas_http_usan_el_cors_comun():
    for nombre, fn in _serverless()["functions"].items():
        for ev in fn.get("events") or []:
            http = ev.get("http") if isinstance(ev, dict) else None
            if http:
                assert http.get("cors") == "${self:custom.cors}", nombre

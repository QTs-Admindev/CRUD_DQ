"""El CORS de las rutas tiene que aceptar X-Actor, o el navegador bloquea las
peticiones del FE que la mandan (y la bitácora vuelve a quedar como 'system').

Se lee serverless.yml como texto a propósito: el CI no instala PyYAML y esta
prueba no justifica sumar una dependencia.
"""
import os
import re

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lineas_sin_comentarios():
    with open(os.path.join(_RAIZ, "serverless.yml"), encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if not ln.lstrip().startswith("#")]


def _bloque_cors_comun(lineas):
    """Las líneas del bloque `custom.cors` (desde `  cors:` hasta el siguiente hermano)."""
    en_custom, dentro, bloque = False, False, []
    for ln in lineas:
        if re.match(r"^custom:\s*$", ln):
            en_custom = True
            continue
        if en_custom and re.match(r"^  cors:\s*$", ln):
            dentro = True
            continue
        if dentro:
            if re.match(r"^  \S", ln) or re.match(r"^\S", ln):
                break
            bloque.append(ln.strip())
    return bloque


def test_el_cors_comun_acepta_x_actor():
    bloque = _bloque_cors_comun(_lineas_sin_comentarios())
    assert bloque, "no existe custom.cors en serverless.yml"
    assert "- X-Actor" in bloque
    assert "- Content-Type" in bloque


def test_todas_las_rutas_usan_el_cors_comun():
    lineas = _lineas_sin_comentarios()
    usos = [ln.strip() for ln in lineas if re.match(r"^\s+cors:\s*\S", ln)]
    assert usos, "ninguna ruta declara cors"
    assert all(u == "cors: ${self:custom.cors}" for u in usos), usos

"""
test_sync_confirma_ambos_lados.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tener el `daijin_id` guardado NO prueba que el activo exista en la plataforma.

El invariante del PR #42 es que un activo se activa **solo con confirmación de la
plataforma**, nunca por el valor de un campo local. El reintento masivo tenía un
atajo que lo rompía: si la fila ya traía `daijin_id`, la activaba sin preguntar.

Ese id puede estar mal por tres caminos que ocurren de verdad:
  - el activo se borró en la plataforma y aquí quedó el id viejo,
  - el id se escribió mal en una importación,
  - la plataforma devolvió otro id al recrearlo.

En los tres, activar a ciegas deja una fila local "activa" apuntando a algo que
no existe, que es justo el estado que el resto del sistema ya no permite.
"""
from unittest.mock import MagicMock, patch

import pytest

from shared.activation import NotOnPlatform, PlatformUnavailable
from functions.sensors import bulk_sync_worker as sensores
from functions.tboxes import bulk_sync_worker as tboxes


FILA_SENSOR = {"id": 10, "sensorCode": "A4C138D844B2", "daijin_id": "999"}
FILA_TBOX = {"id": 20, "tboxCode": "10B41D303F45", "daijin_id": "888"}


@pytest.fixture(params=[
    (sensores, FILA_SENSOR, "sensors"),
    (tboxes, FILA_TBOX, "tboxes"),
], ids=["sensores", "tboxes"])
def modulo(request):
    """Los dos workers son copias del mismo patrón: se prueban los dos."""
    return request.param


# ─── Lo que NO se puede hacer: confiar en el campo local ──────────────────────

def test_no_se_activa_confiando_en_el_daijin_id_guardado(modulo):
    """El atajo que se quitó. Si nadie pregunta a la plataforma, un id basura
    activa la fila igual."""
    mod, fila, recurso = modulo
    with patch.object(mod, "confirm_on_platform") as confirmar:
        confirmar.return_value = "999"
        mod._sync_one(MagicMock(), dict(fila))
    assert confirmar.called, "se activó sin confirmar contra la plataforma"


def test_manda_la_llave_natural_y_el_recurso_correctos(modulo):
    mod, fila, recurso = modulo
    with patch.object(mod, "confirm_on_platform") as confirmar:
        confirmar.return_value = "999"
        mod._sync_one(MagicMock(), dict(fila))
    args, kwargs = confirmar.call_args
    assert args[1] == recurso


# ─── Lo que la plataforma diga es lo que manda ───────────────────────────────

def test_gana_el_id_de_la_plataforma_sobre_el_guardado(modulo):
    """Si el id local no coincide con el de la plataforma, el bueno es el de
    allá: el local pudo escribirse mal."""
    mod, fila, _ = modulo
    with patch.object(mod, "confirm_on_platform", return_value="7777"):
        _id, daijin, err = mod._sync_one(MagicMock(), dict(fila))
    assert daijin == "7777"
    assert err is None


def test_si_el_activo_no_esta_en_la_plataforma_se_vuelve_a_registrar(modulo):
    """El id guardado no corresponde a nada: se descarta y se sigue el camino
    normal, que busca por la llave natural y crea si hace falta."""
    mod, fila, _ = modulo
    with patch.object(mod, "confirm_on_platform", side_effect=NotOnPlatform("no está")), \
         patch.object(mod, "resolve_or_create", return_value="nuevo123") as crear:
        _id, daijin, err = mod._sync_one(MagicMock(), dict(fila))
    assert crear.called, "no se intentó registrar de nuevo"
    assert daijin == "nuevo123"
    assert err is None


# ─── Y lo que más importa: no confundir "no está" con "no pude ver" ──────────

def test_si_no_se_pudo_consultar_NO_se_activa(modulo):
    """Una lectura fallida no es prueba de que el activo no exista. Si se tratara
    como ausencia, se volvería a registrar algo que ya estaba y se acabaría con
    activos duplicados en la plataforma."""
    mod, fila, _ = modulo
    with patch.object(mod, "confirm_on_platform",
                      side_effect=PlatformUnavailable("timeout")), \
         patch.object(mod, "resolve_or_create") as crear:
        _id, daijin, err = mod._sync_one(MagicMock(), dict(fila))

    assert daijin is None, "se activó sin haber podido confirmar"
    assert err is not None, "el fallo de lectura se tragó en silencio"
    assert not crear.called, "se intentó registrar de nuevo sin saber si ya existía"


# ─── La fila sin id sigue funcionando igual que antes ────────────────────────

def test_la_fila_sin_daijin_id_no_pregunta_dos_veces(modulo):
    """Sin id guardado no hay nada que confirmar: va directo a buscar o crear.
    Preguntar antes sería una llamada de más por cada fila importada."""
    mod, fila, _ = modulo
    sin_id = {k: v for k, v in fila.items() if k != "daijin_id"}
    with patch.object(mod, "confirm_on_platform") as confirmar, \
         patch.object(mod, "resolve_or_create", return_value="abc"):
        _id, daijin, err = mod._sync_one(MagicMock(), sin_id)
    assert not confirmar.called
    assert daijin == "abc"

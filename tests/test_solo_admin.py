"""Solo Quinta (compañía 2) crea, edita o borra sensores, Qbox y paquetes.

Las demás compañías solo los asignan o desasignan. La compañía de quien opera
llega en el header X-Company; sin él no se bloquea (llamadas internas y el FE
que aún no lo manda). El guardia corre antes de tocar la base o la plataforma,
así que una compañía cliente recibe 403 sin efectos.
"""
import importlib
import json

import pytest

from shared.auth import SOLO_ADMIN, compania_que_llama, solo_admin

ESCRITURAS = [
    "sensors.create", "sensors.bulk_create", "sensors.update", "sensors.delete",
    "sensors.resync", "sensors.assign",
    "tboxes.create", "tboxes.bulk_create", "tboxes.update", "tboxes.delete",
    "tboxes.resync", "tboxes.assign",
    "packages.create", "packages.edit", "packages.delete", "packages.move",
]

PERMITIDAS = ["packages.assign", "bindings.bind_sensor", "bindings.unbind_sensor",
              "vehicles.bind_tbox", "vehicles.unbind_tbox"]


def _ev(company=None):
    headers = {"X-Company": str(company)} if company is not None else {}
    return {"headers": headers, "pathParameters": {"id": "1"}, "body": "{}"}


def test_lee_la_compania_del_header():
    assert compania_que_llama(_ev(8)) == 8
    assert compania_que_llama({"headers": {"x-company": "2"}}) == 2
    assert compania_que_llama(_ev()) is None
    assert compania_que_llama({"headers": {"X-Company": "abc"}}) is None


def test_guardia():
    assert solo_admin(_ev(8))["statusCode"] == 403
    assert solo_admin(_ev(2)) is None
    assert solo_admin(_ev()) is None


@pytest.mark.parametrize("modulo", ESCRITURAS)
def test_cliente_recibe_403_sin_tocar_nada(modulo, monkeypatch):
    m = importlib.import_module(f"functions.{modulo}")

    def no_debe_llamarse():
        raise AssertionError("tocó la base antes del guardia")

    if hasattr(m, "get_db"):
        monkeypatch.setattr(m, "get_db", no_debe_llamarse)
    resp = m.handler(_ev(8), None)
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"] == SOLO_ADMIN


@pytest.mark.parametrize("modulo", ESCRITURAS)
def test_quinta_pasa_el_guardia(modulo, monkeypatch):
    m = importlib.import_module(f"functions.{modulo}")

    class Paso(Exception):
        pass

    def marca():
        raise Paso()

    if hasattr(m, "get_db"):
        monkeypatch.setattr(m, "get_db", marca)
    try:
        resp = m.handler(_ev(2), None)
    except Paso:
        return
    assert resp["statusCode"] != 403


@pytest.mark.parametrize("modulo", PERMITIDAS)
def test_asignar_y_desasignar_no_tienen_guardia(modulo):
    m = importlib.import_module(f"functions.{modulo}")
    assert "solo_admin" not in vars(m)

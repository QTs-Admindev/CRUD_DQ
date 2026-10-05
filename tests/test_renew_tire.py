"""POST /tires/{id}/renew: la llanta recibe un piso nuevo y sube una vida.

`tires` queda con el estado de la vida nueva (con ese costo se hacen las cuentas)
y el evento 'renovacion' guarda la foto de la vida que termina.
"""
import json

import pytest

from functions.tires import renew as mod


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Store:
    def __init__(self, tire, catalogos):
        self.tire = dict(tire)
        self.catalogos = catalogos
        self.updates = []
        self.events = []

    def get_by_id(self, db, table, rid):
        if table == "tires_catalog":
            c = self.catalogos.get(rid)
            return dict(c) if c else None
        return dict(self.tire) if rid == self.tire["id"] else None

    def update(self, db, table, rid, data):
        self.updates.append(dict(data))
        self.tire.update(data)
        return dict(self.tire)

    def record_event(self, db, **kw):
        self.events.append(kw)
        return {"id": len(self.events), **kw}


LLANTA = {"id": 7, "company_id": 101, "folio": "11", "prefix": "IENTC", "is_deleted": 0,
          "tires_catalog_id": 3, "status": "new", "life_number": 1,
          "cost": 7800, "current_depth": 4, "tire_mileage": 120000,
          "unit_id": 55, "mount_position": 3}
CATALOGOS = {3: {"id": 3, "brand": "Michelin"}, 983: {"id": 983, "brand": "Desconocida"}}


@pytest.fixture
def store(monkeypatch):
    s = Store(LLANTA, CATALOGOS)
    monkeypatch.setattr(mod, "get_db", lambda: FakeDB())
    monkeypatch.setattr(mod, "get_by_id", s.get_by_id)
    monkeypatch.setattr(mod, "update", s.update)
    monkeypatch.setattr(mod, "record_event", s.record_event)
    monkeypatch.setattr(mod, "audit", lambda *a, **k: None)
    return s


def _post(body, rid=7):
    return mod.handler({"pathParameters": {"id": str(rid)}, "body": json.dumps(body),
                        "headers": {"X-Actor": "ana@quinta.tech"}}, None)


def test_renovar_sube_una_vida_y_guarda_la_anterior(store):
    resp = _post({"current_depth": 15, "cost": 3200})
    assert resp["statusCode"] == 200
    # estado actual: la vida nueva
    assert store.tire["status"] == "renewed"
    assert store.tire["life_number"] == 2
    assert store.tire["current_depth"] == 15
    assert store.tire["cost"] == 3200
    # historial: la foto de la vida que termina
    (ev,) = store.events
    assert ev["event_type"] == "renovacion" and ev["life_number"] == 2
    assert ev["prev_cost"] == 7800 and ev["prev_depth_mm"] == 4
    assert ev["prev_mileage_km"] == 120000
    assert ev["cost"] == 3200 and ev["depth_mm"] == 15
    assert ev["actor"] == "ana@quinta.tech"
    assert ev["details"]["prev_life_number"] == 1


def test_renovar_una_renovada_sigue_subiendo(store):
    store.tire.update({"status": "renewed", "life_number": 3})
    assert _post({"current_depth": 14, "cost": 3100})["statusCode"] == 200
    assert store.tire["life_number"] == 4


def test_vida_vacia_cuenta_como_vida_1(store):
    store.tire["life_number"] = None
    assert _post({"current_depth": 14, "cost": 3100})["statusCode"] == 200
    assert store.tire["life_number"] == 2


def test_tope_de_renovadas(store):
    store.tire.update({"status": "renewed", "life_number": 5})
    resp = _post({"current_depth": 14, "cost": 3100})
    assert resp["statusCode"] == 409
    assert store.updates == [] and store.events == []


def test_generica_no_se_renueva(store):
    store.tire["tires_catalog_id"] = 983
    assert _post({"current_depth": 14, "cost": 3100})["statusCode"] == 422
    assert store.updates == []


def test_descartada_no_se_renueva(store):
    store.tire["status"] = "discarded"
    assert _post({"current_depth": 14, "cost": 3100})["statusCode"] == 409


def test_borrada_es_404(store):
    store.tire["is_deleted"] = 1
    assert _post({"current_depth": 14, "cost": 3100})["statusCode"] == 404


@pytest.mark.parametrize("body", [
    {"current_depth": 2.9, "cost": 3100}, {"current_depth": 31, "cost": 3100},
    {"current_depth": 14, "cost": 0}, {"current_depth": 14, "cost": -5},
    {"current_depth": 14}, {"cost": 3100},
    {"current_depth": 14, "cost": 3100, "life_number": 4},
])
def test_datos_invalidos_son_422(store, body):
    assert _post(body)["statusCode"] == 422
    assert store.updates == [] and store.events == []

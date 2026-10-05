"""POST /vehicles/with-package: la unidad se da de alta con su paquete."""
import json

import pytest

from functions.vehicles import create_with_package as mod


def _resp(code, body):
    return {"statusCode": code, "body": json.dumps(body)}


class Store:
    def __init__(self):
        self.packages = {5: {"id": 5, "status": "prepared", "unit_catalog_id": 209, "company_id": 101}}
        self.catalogs = {209: {"id": 209, "axles_count": 1, "tires_axle_1": 2}}
        self.sensors = {5: [{"id": 1}, {"id": 2}]}

    def get_by_id(self, db, table, rid):
        if table.endswith("packages"):
            p = self.packages.get(rid)
            return dict(p) if p else None
        return self.catalogs.get(rid)

    def get_where(self, db, table, where, params, limit=200):
        return self.sensors.get(params[0], [])


@pytest.fixture
def wire(monkeypatch):
    store = Store()
    calls = {"create": [], "assign": []}
    monkeypatch.setattr(mod, "get_db", lambda: object())
    monkeypatch.setattr(mod, "get_by_id", store.get_by_id)
    monkeypatch.setattr(mod, "get_where", store.get_where)
    monkeypatch.setattr(mod, "tire_slots", lambda c: [1, 2])

    def setup(create_resp=None, assign_resp=None):
        def fake_create(ev, ctx):
            calls["create"].append(json.loads(ev["body"]))
            return create_resp or _resp(200, {"id": 77, "unit_identifier": "T-12"})

        def fake_assign(ev, ctx):
            calls["assign"].append((ev["pathParameters"]["id"], json.loads(ev["body"])))
            return assign_resp or _resp(200, {"id": 5, "status": "assigned"})

        monkeypatch.setattr(mod, "vehicle_create_handler", fake_create)
        monkeypatch.setattr(mod, "package_assign_handler", fake_assign)
        return store, calls

    return setup


BODY = {"unit_identifier": "T-12", "company_id": 101, "unit_catalog_id": 209, "package_id": 5}


def _post(**over):
    return mod.handler({"body": json.dumps({**BODY, **over}), "headers": {}}, None)


def test_crea_la_unidad_y_le_asigna_el_paquete(wire):
    store, calls = wire()
    resp = _post()
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["assigned"] is True and data["unit"]["id"] == 77
    # la unidad se crea sin Qbox suelto y el paquete se asigna a la unidad creada
    assert "package_id" not in calls["create"][0] and "tbox_id" not in calls["create"][0]
    assert calls["assign"] == [("5", {"unit_id": 77})]


@pytest.mark.parametrize("mut,code", [
    (lambda s: s.packages.pop(5), 404),
    (lambda s: s.packages[5].update(status="assigned"), 409),
    (lambda s: s.packages[5].update(unit_catalog_id=300), 422),
    (lambda s: s.packages[5].update(company_id=2), 422),
    (lambda s: s.sensors.update({5: [{"id": 1}]}), 422),
])
def test_paquete_no_valido_no_crea_nada(wire, mut, code):
    store, calls = wire()
    mut(store)
    assert _post()["statusCode"] == code
    assert calls["create"] == [] and calls["assign"] == []


def test_no_acepta_qbox_suelto(wire):
    store, calls = wire()
    assert _post(tbox_id=9)["statusCode"] == 422
    assert calls["create"] == []


def test_unidad_pendiente_no_intenta_asignar(wire):
    store, calls = wire(create_resp=_resp(202, {"data": {"id": 77}}))
    resp = _post()
    assert resp["statusCode"] == 202
    assert calls["assign"] == []


def test_error_al_crear_la_unidad_se_propaga(wire):
    store, calls = wire(create_resp=_resp(409, {"error": "ya existe"}))
    assert _post()["statusCode"] == 409
    assert calls["assign"] == []


def test_falla_la_asignacion_avisa_sin_borrar_la_unidad(wire):
    store, calls = wire(assign_resp=_resp(422, {"error": "El paquete tiene 1 sensores"}))
    resp = _post()
    assert resp["statusCode"] == 202
    data = json.loads(resp["body"])["data"]
    assert data["assigned"] is False and data["unit"]["id"] == 77
    assert "sensores" in data["reason"]

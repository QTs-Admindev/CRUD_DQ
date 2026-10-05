"""PUT /tires/{id} guarda los datos de negocio que manda el formulario.

Antes el modelo solo tenía prefix y folio y Pydantic descartaba el resto en
silencio: el FE mandaba catálogo, condición, costo, profundidad y kilometraje, el
endpoint contestaba 200 y la pantalla decía "Llanta actualizada" sin guardar nada.
"""
import json

import pytest

from functions.tires import update as tires_update


class FakeDB:
    def commit(self): pass
    def rollback(self): pass


class Store:
    def __init__(self, tire, catalogos=None):
        self.tire = dict(tire)
        self.catalogos = catalogos or {}
        self.updates = []

    def get_by_id(self, db, table, rid):
        if table == "tires_catalog":
            c = self.catalogos.get(rid)
            return dict(c) if c else None
        return dict(self.tire) if rid == self.tire["id"] else None

    def update(self, db, table, rid, data):
        self.updates.append(dict(data))
        self.tire.update(data)
        return dict(self.tire)


LLANTA = {
    "id": 7, "company_id": 101, "prefix": "IENTC", "folio": "11", "is_deleted": 0,
    "tires_catalog_id": 3, "status": "new", "life_number": 1,
    # Valores viejos fuera de las reglas actuales: el alta ponía profundidad 0.
    "cost": None, "current_depth": 0, "tire_mileage": 0,
}
CATALOGOS = {3: {"id": 3, "max_depth": 20}, 9: {"id": 9, "max_depth": 18}, 12: {"id": 12, "max_depth": None}}


@pytest.fixture
def store(monkeypatch):
    s = Store(LLANTA, CATALOGOS)
    monkeypatch.setattr(tires_update, "get_db", lambda: FakeDB())
    monkeypatch.setattr(tires_update, "get_by_id", s.get_by_id)
    monkeypatch.setattr(tires_update, "update", s.update)
    monkeypatch.setattr(tires_update, "get_where", lambda *a, **k: [])
    monkeypatch.setattr(tires_update, "audit", lambda *a, **k: None)
    return s


def _put(body, rid=7):
    return tires_update.handler({"pathParameters": {"id": str(rid)}, "body": json.dumps(body)}, None)


def _formulario(**cambios):
    """Lo que manda hoy TiresPage al editar: todos los campos, cambien o no."""
    base = {"folio": "11", "prefix": "IENTC", "company_id": 101, "status": "new",
            "life_number": 1, "tires_catalog_id": 3, "current_depth": 0, "tire_mileage": 0}
    base.update(cambios)
    return base


def test_guarda_catalogo_condicion_costo_profundidad_y_kilometraje(store):
    resp = _put(_formulario(tires_catalog_id=9, status="used", life_number=1,
                            cost=5400.5, current_depth=12.25, tire_mileage=85000))
    assert resp["statusCode"] == 200
    guardado = store.updates[-1]
    assert guardado["tires_catalog_id"] == 9
    assert guardado["status"] == "used"
    assert guardado["cost"] == 5400.5
    assert guardado["current_depth"] == 12.25
    assert guardado["tire_mileage"] == 85000


def test_editar_solo_el_folio_no_se_bloquea_por_valores_viejos(store):
    # La llanta tiene profundidad 0 (fuera de 3..30); el FE la reenvía sin tocarla.
    resp = _put(_formulario(folio="12"))
    assert resp["statusCode"] == 200
    guardado = store.updates[-1]
    assert guardado["folio"] == "12"
    assert "current_depth" not in guardado and "status" not in guardado


def test_un_campo_desconocido_es_422_y_no_se_ignora(store):
    resp = _put({"folio": "11", "unit_id": 5})
    assert resp["statusCode"] == 422
    assert store.updates == []


def test_no_mueve_la_llanta_de_compania(store):
    resp = _put(_formulario(company_id=133, current_depth=10))
    assert resp["statusCode"] == 422
    assert store.updates == []


def test_catalogo_inexistente_es_422(store):
    resp = _put(_formulario(tires_catalog_id=999))
    assert resp["statusCode"] == 422
    assert store.updates == []


@pytest.mark.parametrize("campo,valor", [
    ("current_depth", 2.5), ("current_depth", 31), ("current_depth", 10.123),
    ("tire_mileage", -1), ("tire_mileage", 600001), ("tire_mileage", 100.5),
    ("cost", -1), ("cost", 1_000_001), ("cost", 10.999),
])
def test_valores_fuera_de_regla_son_422(store, campo, valor):
    resp = _put(_formulario(**{campo: valor}))
    assert resp["statusCode"] == 422
    assert store.updates == []


def test_profundidad_no_pasa_la_de_fabrica_del_catalogo(store):
    assert _put(_formulario(current_depth=21))["statusCode"] == 422      # catálogo 3: 20 mm
    assert _put(_formulario(current_depth=19.5))["statusCode"] == 200


def test_la_de_fabrica_es_la_del_catalogo_nuevo_si_tambien_cambia(store):
    assert _put(_formulario(tires_catalog_id=9, current_depth=19))["statusCode"] == 422  # 9: 18 mm


def test_catalogo_sin_profundidad_de_fabrica_no_bloquea(store):
    assert _put(_formulario(tires_catalog_id=12, current_depth=25))["statusCode"] == 200


# Editar solo corrige nueva <-> gallito. Renovada y la vida van por Renovar.
@pytest.mark.parametrize("status,vida,esperado", [
    ("renewed", 2, 422), ("renewed", 5, 422), ("renewed", 1, 422), ("renewed", 6, 422),
    ("used", 1, 200), ("used", 2, 422), ("discarded", 1, 422),
])
def test_condicion(store, status, vida, esperado):
    assert _put(_formulario(status=status, life_number=vida))["statusCode"] == esperado


def test_sin_cambios_no_escribe(store):
    store.tire["current_depth"] = 10
    resp = _put({"current_depth": 10.0})
    assert resp["statusCode"] == 200
    assert store.updates == []


def test_una_renovada_no_vuelve_a_nueva(store):
    store.tire.update({"status": "renewed", "life_number": 3})
    resp = _put(_formulario(status="new", life_number=3))
    assert resp["statusCode"] == 422
    assert store.updates == []


def test_una_renovada_no_cambia_de_vida_editando(store):
    store.tire.update({"status": "renewed", "life_number": 3})
    resp = _put(_formulario(status="renewed", life_number=4))
    assert resp["statusCode"] == 422


def test_una_renovada_se_edita_si_no_toca_la_vida(store):
    store.tire.update({"status": "renewed", "life_number": 3})
    resp = _put(_formulario(status="renewed", life_number=3, folio="12"))
    assert resp["statusCode"] == 200
    assert store.updates[-1]["folio"] == "12"


# ---------- confirmar la condición de una llanta "sin confirmar" ----------

@pytest.fixture
def eventos(monkeypatch):
    got = []
    monkeypatch.setattr(tires_update, "record_event", lambda db, **kw: got.append(kw) or kw)
    return got


def test_confirmar_nueva_aunque_no_cambie(store, eventos, monkeypatch):
    monkeypatch.setattr(tires_update, "_sin_confirmar", lambda db, tid: True)
    resp = _put({"status": "new", "confirm_condition": True})
    assert resp["statusCode"] == 200
    (ev,) = eventos
    assert ev["event_type"] == "confirmacion" and ev["origin"] == "new" and ev["life_number"] == 1


def test_confirmar_renovada_con_su_vida(store, eventos, monkeypatch):
    monkeypatch.setattr(tires_update, "_sin_confirmar", lambda db, tid: True)
    resp = _put({"status": "renewed", "life_number": 3, "confirm_condition": True})
    assert resp["statusCode"] == 200
    assert store.tire["status"] == "renewed" and store.tire["life_number"] == 3
    assert eventos[0]["origin"] == "renewed"


def test_confirmar_dos_veces_es_409(store, eventos, monkeypatch):
    monkeypatch.setattr(tires_update, "_sin_confirmar", lambda db, tid: False)
    resp = _put({"status": "used", "confirm_condition": True})
    assert resp["statusCode"] == 409
    assert store.updates == [] and eventos == []


@pytest.mark.parametrize("body", [
    {"confirm_condition": True},
    {"status": "renewed", "confirm_condition": True},
    {"status": "renewed", "life_number": 6, "confirm_condition": True},
    {"status": "discarded", "confirm_condition": True},
])
def test_confirmar_con_datos_invalidos_es_422(store, eventos, monkeypatch, body):
    monkeypatch.setattr(tires_update, "_sin_confirmar", lambda db, tid: True)
    assert _put(body)["statusCode"] == 422
    assert eventos == []

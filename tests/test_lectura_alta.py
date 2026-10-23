"""Profundidad del alta en Loads: cuándo se escribe, con qué forma y que nunca tumba nada."""
from datetime import datetime, timezone

import pytest

import shared.lectura_alta as la

CAT_UNIDAD = {"id": 7, "axles_count": 3, "tires_axle_1": 2, "tires_axle_2": 4, "tires_axle_3": 4}
AHORA = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)


def _llanta(**kw):
    base = {"id": 501, "daijin_id": "9001", "sensor_id": 33, "unit_id": 1296, "company_id": 136,
            "tires_catalog_id": 209, "current_depth": 18.5, "axle_index": 2, "wheel_index": 3,
            "prefix": "QT", "folio": "0042", "is_deleted": 0}
    return {**base, **kw}


UNIDAD = {"id": 1296, "daijin_id": "25112", "unit_catalog_id": 7, "status": "active",
          "unit_identifier": "ECO-12"}


class Loads:
    def __init__(self, falla=False):
        self.escritos, self.falla = [], falla

    def update_one(self, filtro, cambio, upsert):
        if self.falla:
            raise TimeoutError("Mongo no responde")
        self.escritos.append((filtro, cambio, upsert))


@pytest.fixture
def entorno(monkeypatch):
    filas = {
        ("tires", 501): _llanta(),
        ("tires_catalog", 209): {"id": 209, "brand": "Michelin"},
        ("tires_catalog", 983): {"id": 983, "brand": "Desconocida"},
        ("units", 1296): dict(UNIDAD),
        ("unit_catalog", 7): CAT_UNIDAD,
        ("companies", 136): {"id": 136, "company_name": "DemoTech"},
        ("sensors", 33): {"id": 33, "sensorCode": "A1B2C3D4E5F6"},
    }
    loads = Loads()
    monkeypatch.setattr(la, "get_by_id", lambda db, tabla, rid: filas.get((tabla, rid)))
    monkeypatch.setattr(la, "_loads", lambda: loads)
    return filas, loads


def test_posicion_real_igual_que_gpshook():
    assert la.posicion_real(CAT_UNIDAD, 1, 1) == 1
    assert la.posicion_real(CAT_UNIDAD, 2, 3) == 5
    assert la.posicion_real(CAT_UNIDAD, 3, 4) == 10
    assert la.posicion_real(CAT_UNIDAD, 0, 1) is None  # repuesto: GPSHook tampoco la posiciona
    assert la.posicion_real(CAT_UNIDAD, 2, 5) is None
    assert la.posicion_real(None, 1, 1) is None


def test_documento_como_una_lectura_real():
    d = la.documento(_llanta(), UNIDAD, CAT_UNIDAD, {"company_name": "DemoTech"},
                     {"sensorCode": "A1B2C3D4E5F6"}, AHORA)
    assert d["_id"] == "alta-501" and d["origen"] == "alta"
    assert d["tyreCode"] == "501" and d["tyreId"] == 9001 and d["vehicleId"] == 25112
    assert d["licensePlateNumber"] == "1296" and d["realPosition"] == 5
    assert d["nowThreadDepth"] == 18.5
    assert d["calculateTime"] == d["receiveTime"] == "2026-10-24 02:00:00"  # hora de China
    assert d["tag"] == "QT-0042" and d["company_name"] == "DemoTech"
    assert "alerts" not in d  # las alertas solo las abre GPSHook


@pytest.mark.parametrize("falta", [
    {"current_depth": 0}, {"current_depth": None}, {"daijin_id": None},
    {"axle_index": 0}, {"wheel_index": None},
])
def test_documento_sin_datos_completos_no_sale(falta):
    assert la.documento(_llanta(**falta), UNIDAD, CAT_UNIDAD, None, None, AHORA) is None


def test_sembrar_escribe_una_sola_vez_por_llanta(entorno):
    _, loads = entorno
    assert la.sembrar(None, 501) is True
    filtro, cambio, upsert = loads.escritos[0]
    assert filtro == {"_id": "alta-501"} and upsert is True
    assert list(cambio) == ["$setOnInsert"]  # reintentos y re-montajes no la pisan


@pytest.mark.parametrize("cambio", [
    {"sensor_id": None}, {"unit_id": None}, {"is_deleted": 1}, {"tires_catalog_id": 983},
])
def test_sin_sensor_sin_montar_borrada_o_generica_no_escribe(entorno, cambio):
    filas, loads = entorno
    filas[("tires", 501)].update(cambio)
    assert la.sembrar(None, 501) is False
    assert loads.escritos == []


def test_mongo_caido_no_tumba_nada(entorno, monkeypatch):
    monkeypatch.setattr(la, "_loads", lambda: Loads(falla=True))
    assert la.sembrar(None, 501) is False


def test_sin_secreto_no_toca_mongo(monkeypatch):
    monkeypatch.delenv("MONGO_SECRET", raising=False)
    assert la._loads() is None

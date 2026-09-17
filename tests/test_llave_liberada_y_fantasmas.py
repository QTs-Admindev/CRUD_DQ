"""
test_llave_liberada_y_fantasmas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Dos huecos que dejaban activos inservibles sin que nadie se enterara.

1. AL BORRAR, EL FOLIO SEGUÍA OCUPADO

El borrado es lógico: la fila se queda con `is_deleted=1`. Pero las llaves UNIQUE
no saben de eso, así que la fila borrada sigue ocupando su folio o su código.

Medido en producción: 70 folios de llanta y 23 códigos de sensor ocupados por filas
ya borradas.

Matiz importante: los creates YA se recuperaban de esto, pero de forma REACTIVA
(chocar contra el índice, liberar la llave del muerto, reintentar el insert). Eso
funciona para el alta, y no para nada más: cualquiera que mire la llave antes de
insertar la ve ocupada. Ahora se libera al borrar, y el camino reactivo del create
queda como red por si acaso, con la misma marca.

2. LO QUE ESTÁ EN INVENTARIO NO SE REVISABA NUNCA

El cron alcanza a un activo a través de lo que tiene armado: la llanta porque
está montada, el sensor porque está en una llanta. Un activo que no está armado
con nada no tiene por dónde ser alcanzado.

Medido: 2,073 activos con id guardado que nadie revisaba, el 19 % del total. Si
ese id quedó mal o el activo se borró en la plataforma, se descubre en el peor
momento: al intentar usarlo.
"""
from unittest.mock import MagicMock, patch

import pytest

from shared import activation as activacion
from shared.activation import (
    NotOnPlatform, PlatformUnavailable, liberar_llave_natural, llave_original,
)
from shared.smarttyre.basic_api import DONE
from functions.reconciliation import reconcile
from functions.sensors import delete as sdel
from functions.tboxes import delete as bdel
from functions.tires import delete as tdel
from functions.vehicles import delete as vdel

from tests.test_soft_delete import FakeDB, FakeRemote, FakeStore, _wire


# ─── 1. Liberar la llave al borrar ────────────────────────────────────────────

@pytest.mark.parametrize("recurso,campo,valor", [
    ("tires", "folio", "202"),
    ("sensors", "sensorCode", "A4C138D844B2"),
    ("units", "unit_identifier", "EC1054"),
    ("tboxes", "tboxCode", "10B41D303F45"),
])
def test_la_llave_queda_libre_para_reusarse(recurso, campo, valor):
    campos = liberar_llave_natural({"id": 4711, campo: valor}, recurso)
    assert campos[campo] != valor, "la llave sigue ocupando el índice"
    assert campos[campo].startswith(valor)


def test_el_valor_original_se_puede_recuperar():
    """Hace falta para la auditoría: tiene que quedar rastro de QUÉ se borró."""
    campos = liberar_llave_natural({"id": 4711, "folio": "202"}, "tires")
    assert llave_original(campos["folio"]) == "202"


def test_dos_borrados_del_mismo_folio_no_chocan_entre_si():
    """Si la marca fuera fija, el segundo borrado del folio 202 chocaría con el
    primero y el borrado fallaría. Por eso lleva el id dentro."""
    a = liberar_llave_natural({"id": 1, "folio": "202"}, "tires")["folio"]
    b = liberar_llave_natural({"id": 2, "folio": "202"}, "tires")["folio"]
    assert a != b


def test_liberar_dos_veces_no_encadena_marcas():
    """El borrado puede reintentarse. Sin esto quedaría `202#del-1#del-1`, y a la
    tercera se pasaría del largo de la columna."""
    una = liberar_llave_natural({"id": 1, "folio": "202"}, "tires")
    assert liberar_llave_natural({"id": 1, **una}, "tires") == {}


def test_una_llave_larga_no_desborda_la_columna():
    """Las columnas son varchar(255). Perder cola del folio es preferible a que
    el borrado falle por longitud."""
    campos = liberar_llave_natural({"id": 999999, "folio": "X" * 300}, "tires")
    assert len(campos["folio"]) <= 255
    assert campos["folio"].endswith("#del-999999")


def test_sin_llave_no_se_inventa_nada():
    assert liberar_llave_natural({"id": 1, "folio": None}, "tires") == {}
    assert liberar_llave_natural({"id": 1, "folio": ""}, "tires") == {}
    assert liberar_llave_natural({"id": 1}, "recurso_desconocido") == {}


# ─── 1b. Que el borrado real la libere, no solo la función suelta ────────────

def test_el_folio_queda_libre_al_borrar_la_llanta(monkeypatch):
    """La queja original: se borra la llanta y el folio sigue ocupado."""
    store = FakeStore({7: {"id": 7, "is_deleted": 0, "folio": "202", "daijin_id": "33"}})
    _wire(monkeypatch, tdel, store, FakeRemote((DONE, None)))

    resp = tdel.handler({"pathParameters": {"id": "7"}}, None)

    assert resp["statusCode"] == 200
    assert store.rows[7]["folio"] != "202", "el folio sigue ocupando el índice UNIQUE"


def test_el_codigo_queda_libre_al_borrar_el_sensor(monkeypatch):
    store = FakeStore({8: {"id": 8, "is_deleted": 0, "sensorCode": "A4C138D844B2",
                           "tire_id": None, "daijin_id": "44"}})
    _wire(monkeypatch, sdel, store, FakeRemote((DONE, None)))

    resp = sdel.handler({"pathParameters": {"id": "8"}}, None)

    assert resp["statusCode"] == 200
    assert store.rows[8]["sensorCode"] != "A4C138D844B2"


def test_el_codigo_queda_libre_al_borrar_el_qbox(monkeypatch):
    store = FakeStore({9: {"id": 9, "is_deleted": 0, "tboxCode": "10B41D303F45",
                           "daijin_id": "34616"}})
    _wire(monkeypatch, bdel, store, FakeRemote((DONE, None)))

    resp = bdel.handler({"pathParameters": {"id": "9"}}, None)

    assert resp["statusCode"] == 200
    assert store.rows[9]["tboxCode"] != "10B41D303F45"


def test_el_identificador_queda_libre_al_borrar_la_unidad(monkeypatch):
    store = FakeStore({1: {"id": 1, "is_deleted": 0, "tbox_id": None,
                           "unit_identifier": "EC1054", "daijin_id": "33"}})
    _wire(monkeypatch, vdel, store, FakeRemote((DONE, None)))

    resp = vdel.handler({"pathParameters": {"id": "1"}}, None)

    assert resp["statusCode"] == 200
    assert store.rows[1]["unit_identifier"] != "EC1054"


def test_el_borrado_pendiente_libera_la_llave_al_completarse(monkeypatch):
    """Si la plataforma estaba caída, la fila queda `is_deleted=1` con su id y su
    llave intactos: el cron los necesita para buscarla allá. La llave se libera
    cuando el borrado se completa de verdad, no antes."""
    fila = {"id": 2, "daijin_id": "99", "folio": "202"}
    updates = []

    def solo_borrados_de_llanta(db, table, where, params=(), limit=100, order="ASC"):
        if table.endswith("tires") and "is_deleted = 1" in where:
            return [dict(fila)]
        return []

    monkeypatch.setattr(reconcile, "get_db", lambda: FakeDB())
    monkeypatch.setattr(reconcile, "SmartTyreClient", lambda: object())
    monkeypatch.setattr(reconcile, "get_where", solo_borrados_de_llanta)
    monkeypatch.setattr(reconcile, "_find_id", lambda *a: None)
    monkeypatch.setattr(reconcile, "attempt_delete", lambda *a, **k: (reconcile.DONE, None))
    monkeypatch.setattr(reconcile, "update",
                        lambda db, table, rid, data: updates.append((rid, data)))

    out = reconcile.handler({}, None)

    assert out["deleted"] == 1
    _rid, data = updates[0]
    assert data["daijin_id"] is None
    assert data.get("folio") not in (None, "202"), "el folio siguió ocupado al completarse"


# ─── 2. El barrido que revisa lo que nadie miraba ────────────────────────────

def test_la_rebanada_avanza_con_el_reloj():
    """Sin estado entre corridas: qué revisar sale del reloj y del id."""
    ahora = 1_800_000_000
    assert reconcile._rebanada_actual(ahora) != reconcile._rebanada_actual(ahora + 300)


def test_la_rebanada_siempre_cae_dentro_del_rango():
    """La rebanada se compara contra `id % VERIFY_PERIOD`. Si se saliera del rango,
    la consulta no devolvería nunca una fila y el barrido quedaría mudo sin fallar."""
    base = 1_800_000_000
    for i in range(0, reconcile.VERIFY_PERIOD * 2, 7):
        assert 0 <= reconcile._rebanada_actual(base + 300 * i) < reconcile.VERIFY_PERIOD


def test_una_vuelta_completa_cubre_a_todos_y_luego_se_repite():
    """Cada activo tiene que llegarle el turno, y el ciclo tiene que cerrar: si no
    volviera al principio, las rebanadas altas no se revisarían nunca otra vez."""
    base = 1_800_000_000
    vistas = [reconcile._rebanada_actual(base + 300 * i)
              for i in range(reconcile.VERIFY_PERIOD)]
    assert len(set(vistas)) == reconcile.VERIFY_PERIOD
    assert reconcile._rebanada_actual(base + 300 * reconcile.VERIFY_PERIOD) == vistas[0]


def _cfg(tabla="sensors"):
    """La configuración REAL del cron, no una inventada.

    Importa: `resource` ("sensor") y `table` ("sensors") son nombres distintos y solo
    el segundo sirve para preguntar por la llave natural. Con un cfg de mentiras el
    barrido parecería funcionar aquí y no haría nada en producción.
    """
    return next(c for c in reconcile.ASSETS if c["table"] == tabla)


def _correr(filas, resultado, tabla="sensors"):
    """Corre el barrido con la plataforma simulada.

    Devuelve (updates, summary, espía de la consulta a la plataforma).
    """
    updates = []
    summary = {"errors": 0}
    with patch.object(reconcile, "get_where", return_value=filas), \
         patch.object(reconcile, "update",
                      lambda db, tabla_, rid, data: updates.append((rid, data))), \
         patch.object(reconcile, "confirm_on_platform", side_effect=resultado) as consulta:
        reconcile._sweep_phantom_ids(MagicMock(), MagicMock(), tabla, _cfg(tabla), summary)
    return updates, summary, consulta


FILA = {"id": 10, "daijin_id": "555", "sensorCode": "A4C138D844B2"}


def test_se_pregunta_por_un_recurso_que_la_plataforma_conoce():
    """`confirm_on_platform` lanza ValueError con un recurso que no conoce, y ese
    error se lo traga el except genérico: el barrido contaría errores en silencio."""
    _updates, _summary, consulta = _correr([dict(FILA)], lambda *a, **k: "555")
    assert consulta.call_args[0][1] in activacion.LOOKUP


def test_si_el_activo_existe_no_se_toca_nada():
    updates, summary, _ = _correr([dict(FILA)], lambda *a, **k: "555")
    assert updates == []
    assert summary["verified"] == 1


def test_un_id_fantasma_se_limpia_y_la_fila_vuelve_a_registrarse():
    """No se re-registra aquí: se devuelve la fila a su estado real y el barrido
    que ya existe la recoge en la corrida siguiente."""
    updates, summary, _ = _correr(
        [dict(FILA)], lambda *a, **k: (_ for _ in ()).throw(NotOnPlatform("no está")))
    assert len(updates) == 1
    _rid, data = updates[0]
    assert data["daijin_id"] is None
    assert data["status"] == "registering"
    assert summary["phantom_cleared"] == 1


def test_si_la_plataforma_tiene_otro_id_gana_el_de_la_plataforma():
    """El id local pudo escribirse mal en una importación."""
    updates, summary, _ = _correr([dict(FILA)], lambda *a, **k: "7777")
    assert updates[0][1]["daijin_id"] == "7777"
    assert summary["phantom_healed"] == 1


def test_si_no_se_pudo_preguntar_NO_se_borra_el_id():
    """El error más caro de todos: tratar un timeout como ausencia borraría el id
    de un activo sano y lo mandaría a registrarse de nuevo, duplicándolo en la
    plataforma."""
    updates, summary, _ = _correr(
        [dict(FILA)], lambda *a, **k: (_ for _ in ()).throw(PlatformUnavailable("timeout")))
    assert updates == [], "se tocó la fila sin haber podido confirmar"
    assert summary["verify_skipped"] == 1


def test_una_fila_mala_no_tumba_a_las_demas():
    filas = [dict(FILA, id=1), dict(FILA, id=2), dict(FILA, id=3)]
    llamadas = {"n": 0}

    def plataforma(*a, **k):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("algo raro")
        return "555"

    _updates, summary, _ = _correr(filas, plataforma)
    assert summary["verified"] == 2
    assert summary["errors"] == 1


def test_solo_se_revisa_la_rebanada_que_toca_y_con_tope():
    """Sin el filtro por rebanada, cada corrida preguntaría por los 11,000 activos."""
    with patch.object(reconcile, "get_where", return_value=[]) as consulta, \
         patch.object(reconcile, "confirm_on_platform"):
        reconcile._sweep_phantom_ids(MagicMock(), MagicMock(), "sensors",
                                     _cfg(), {"errors": 0})
    _db, _tabla, where, params, tope = consulta.call_args[0]
    assert "is_deleted = 0" in where and "id %" in where
    assert params[0] == reconcile._rebanada_actual()
    assert tope == reconcile.VERIFY_MAX


def test_no_se_toca_lo_que_un_usuario_acaba_de_modificar():
    """La plataforma tarda en dejar ver lo recién insertado; por eso el alta hace GET
    después del POST. Sin enfriamiento, una llanta creada hace 30 segundos daría
    "no está" y el barrido le borraría el id a un activo sano."""
    with patch.object(reconcile, "get_where", return_value=[]) as consulta,          patch.object(reconcile, "confirm_on_platform"):
        reconcile._sweep_phantom_ids(MagicMock(), MagicMock(), "sensors",
                                     _cfg(), {"errors": 0})
    _db, _tabla, where, params, _tope = consulta.call_args[0]
    assert "updated_at" in where, "el barrido no respeta el enfriamiento"
    corte = params[-1]
    assert corte <= reconcile.now_ms() - reconcile.COOLOFF_MS


# ─── 1c. Una sola marca para todo el repo ────────────────────────────────────

def test_ningun_camino_inventa_su_propia_marca():
    """Los seis creates ya liberaban la llave al chocar, pero con OTRA marca
    (`__del{id}`). Dos formatos para lo mismo significa que `llave_original` solo
    recupera la mitad de los casos y que la auditoría muestra folios a medias.
    """
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parents[1]
    culpables = [str(p.relative_to(raiz))
                 for p in list((raiz / "functions").rglob("*.py")) + [raiz / "shared" / "activation.py"]
                 if "__del" in p.read_text(encoding="utf-8")]
    assert culpables == [], f"marca propia en: {culpables}"

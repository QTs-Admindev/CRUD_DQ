-- ============================================================================
--  Migración: Historial de llantas (`tire_events`)
--
--  Cada cosa que le pasa a una llanta queda como un renglón: alta, renovación y,
--  más adelante, montaje, desmontaje, pérdida, reventón, baja y edición. Solo
--  se agregan renglones; nunca se editan ni se borran.
--
--  `tires` sigue siendo el ESTADO ACTUAL (con el que se hacen las cuentas):
--  `tires.cost` es el costo de la vida actual y `current_depth` sus mm. El
--  historial guarda lo anterior para no perderlo.
--
--  Esta migración:
--    - CREATE TABLE tire_events (tabla nueva).
--    - Relleno: un evento "alta" por cada llanta viva, con su condición actual.
--      Las de folio PKG... (creadas por paquetes) quedan con origen "sin confirmar".
--    - Normaliza tires.life_number: nuevas y gallitos (new/used) con 0 o NULL
--      pasan a vida 1. Las renovadas conservan la suya. Es el ÚNICO cambio a
--      datos existentes.
--
--  Medido en prod el 5-oct-2026 (solo lectura): 5,007 llantas vivas; 4,882 con
--  life_number 0/NULL; 46 renovadas (45 en vida 2, 1 en vida 3); 13 folios PKG.
--
--  ⚠️ REQUISITOS ANTES DE EMPEZAR:
--   1. Usuario ADMIN/MASTER del RDS (app_user no tiene DDL). Correr en DBeaver.
--   2. SNAPSHOT del RDS desde la consola de AWS (además del backup de abajo).
--   3. Ventana de bajo tráfico.
--
--  ⚠️ El CREATE TABLE hace auto-commit. El backup del PASO 0 es la red de
--     seguridad del UPDATE de life_number.
--
--  DEV: el stage dev usa prefijo `test_`. Para correrla allá, reemplaza en TODO
--  el archivo `tires` por `test_tires` y `tire_events` por `test_tire_events`
--  (tires_catalog no lleva prefijo).
--
--  Correr PASO por PASO, revisando las verificaciones antes de avanzar.
-- ============================================================================


-- ############################################################################
--  PASO 0 — BACKUP de lo que se va a modificar (solo id + life_number)
--  >>> Si corres en otra fecha, cambia el sufijo _20261005 en TODO el archivo. <<<
-- ############################################################################

CREATE TABLE tires_vida_bak_20261005 AS SELECT id, life_number FROM tires;

SELECT (SELECT COUNT(*) FROM tires) AS orig, (SELECT COUNT(*) FROM tires_vida_bak_20261005) AS bak;
-- >>> NO CONTINUAR si orig y bak no coinciden. <<<


-- ############################################################################
--  PASO 1 — Tabla nueva `tire_events`
--  Timestamps en epoch ms, como el resto de la BD.
--
--  event_type: 'alta' | 'renovacion' (por ahora). Después: 'montaje',
--              'desmontaje', 'perdida', 'reventon', 'baja', 'edicion', 'ajuste_vida'.
--  origin    : solo en 'alta': 'new' | 'used' (gallito) | 'renewed' | 'unconfirmed'.
--  life_number, depth_mm, cost: cómo QUEDA la llanta después del evento.
--  prev_*    : en 'renovacion', la foto de la vida que termina (costo, mm, km).
-- ############################################################################

CREATE TABLE tire_events (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tire_id         BIGINT UNSIGNED NOT NULL,
  company_id      INT             NULL,
  event_type      VARCHAR(32)     NOT NULL,
  occurred_at     BIGINT          NOT NULL,
  actor           VARCHAR(255)    NULL,
  origin          VARCHAR(16)     NULL,
  life_number     INT             NULL,
  depth_mm        FLOAT           NULL,
  mileage_km      FLOAT           NULL,
  cost            FLOAT           NULL,
  prev_cost       FLOAT           NULL,
  prev_depth_mm   FLOAT           NULL,
  prev_mileage_km FLOAT           NULL,
  unit_id         INT             NULL,
  mount_position  INT             NULL,
  details         JSON            NULL,
  created_at      BIGINT          NOT NULL,
  PRIMARY KEY (id),
  KEY idx_tire_events_tire    (tire_id, occurred_at),
  KEY idx_tire_events_company (company_id, occurred_at),
  KEY idx_tire_events_type    (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SHOW CREATE TABLE tire_events;


-- ############################################################################
--  PASO 2 — Normalizar life_number (nuevas y gallitos sin vida -> vida 1)
-- ############################################################################

-- Antes: cuántas se van a tocar (esperado ~4,882 en prod).
SELECT COUNT(*) AS a_normalizar FROM tires
 WHERE status IN ('new', 'used') AND (life_number IS NULL OR life_number = 0);

UPDATE tires SET life_number = 1
 WHERE status IN ('new', 'used') AND (life_number IS NULL OR life_number = 0);

-- Después: debe dar 0.
SELECT COUNT(*) AS pendientes FROM tires
 WHERE status IN ('new', 'used') AND (life_number IS NULL OR life_number = 0);

-- Renovadas sin vida válida (debería ser 0; si no, revisar a mano, no se tocan).
SELECT id, folio, life_number FROM tires
 WHERE status = 'renewed' AND (life_number IS NULL OR life_number < 2);


-- ############################################################################
--  PASO 3 — Relleno: un evento 'alta' por cada llanta viva
--  Costo y mm en 0 se guardan como NULL ("no capturado").
--  Idempotente: no duplica si la llanta ya tiene su 'alta'.
-- ############################################################################

INSERT INTO tire_events
  (tire_id, company_id, event_type, occurred_at, actor, origin, life_number,
   depth_mm, mileage_km, cost, unit_id, mount_position, details, created_at)
SELECT
  t.id,
  t.company_id,
  'alta',
  COALESCE(t.created_at, t.updated_at, UNIX_TIMESTAMP() * 1000),
  'migracion',
  CASE
    WHEN t.folio LIKE 'PKG%'            THEN 'unconfirmed'
    WHEN t.status = 'renewed'           THEN 'renewed'
    WHEN t.status = 'used'              THEN 'used'
    WHEN t.status = 'new'               THEN 'new'
    ELSE 'unconfirmed'
  END,
  t.life_number,
  NULLIF(t.current_depth, 0),
  NULLIF(t.tire_mileage, 0),
  NULLIF(t.cost, 0),
  t.unit_id,
  t.mount_position,
  JSON_OBJECT('backfill', TRUE, 'status', t.status),
  UNIX_TIMESTAMP() * 1000
FROM tires t
WHERE (t.is_deleted IS NULL OR t.is_deleted = 0)
  AND NOT EXISTS (SELECT 1 FROM tire_events e WHERE e.tire_id = t.id AND e.event_type = 'alta');

-- Verificación: una 'alta' por llanta viva, y el reparto por origen.
SELECT
  (SELECT COUNT(*) FROM tires WHERE is_deleted IS NULL OR is_deleted = 0) AS vivas,
  (SELECT COUNT(*) FROM tire_events WHERE event_type = 'alta')            AS altas;
SELECT origin, COUNT(*) FROM tire_events WHERE event_type = 'alta' GROUP BY origin;
-- >>> vivas y altas deben coincidir. <<<


-- ############################################################################
--  ROLLBACK (solo si algo salió mal)
-- ############################################################################
-- UPDATE tires t JOIN tires_vida_bak_20261005 b ON b.id = t.id SET t.life_number = b.life_number;
-- DROP TABLE tire_events;
-- (cuando todo esté verificado y estable:) DROP TABLE tires_vida_bak_20261005;

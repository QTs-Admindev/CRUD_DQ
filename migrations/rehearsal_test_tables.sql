-- =============================================================================
-- ENSAYO DE MIGRACIÓN — tablas test_ en la misma BD `quinta`
-- =============================================================================
-- Objetivo: validar ADD COLUMN daijin_id + backfill + verificación sobre CLONES
--           de las tablas reales, sin tocar producción.
--
-- REQUISITOS:
--   - Usuario con privilegios CREATE, ALTER, DROP (el `app_user` NO los tiene;
--     usar el master/admin del RDS).
--   - Los triggers (STEP F) además requieren TRIGGER + log_bin_trust_function_creators=ON
--     (hoy está OFF en el RDS). Por eso van marcados como OPCIONAL.
--
-- SEGURIDAD:
--   - Todo opera sobre tablas `test_*`. Las tablas reales solo se LEEN en STEP A.
--   - `CREATE TABLE ... LIKE` NO copia foreign keys → los clones quedan aislados.
--   - Al terminar, STEP G borra todo. Nada queda permanente.
--
-- CÓMO USAR: ejecuta por STEP, revisa la salida de STEP D y mándamela como feedback.
-- =============================================================================
--
-- -----------------------------------------------------------------------------
-- ALCANCE Y NOTAS (validado contra la lista real de 53 tablas)
-- -----------------------------------------------------------------------------
-- EN ALCANCE (8 tablas) — donde vive el match quinta_id <-> daijin_id:
--   Activos:  units, tires, sensors, tboxes
--   Mapping:  unit_id_mapping, tire_id_mapping, sensor_id_mapping, tbox_id_mapping
--   Verificado en information_schema: NINGUNA otra tabla tiene columna daijin/smarttyre,
--   así que el concepto del match está 100% contenido en estas 8.
--
-- FUERA DE ALCANCE (revisadas, no afectan la migración de daijin_id):
--   trucks / trucks_catalog      -> LEGACY ABANDONADOS (confirmado). La tabla viva
--                                   de vehículos es `units`. No entran a la migración.
--   sensor_sheet_sync /
--   tbox_sheet_sync              -> sincronización a Google Sheets, sin columna daijin.
--   admin_sensor / admin_tbox    -> subsistema admin/instalación, dominio distinto.
--   tire_renewals /
--   warehouse_tires              -> ciclo de vida de llantas; no tocan el mapping.
-- -----------------------------------------------------------------------------


-- =============================================================================
-- STEP A — Crear clones test_ con datos reales  (lee tablas reales, no las modifica)
-- =============================================================================
-- Limpieza previa: permite re-ejecutar STEP A sin "table already exists".
DROP TABLE IF EXISTS
  test_units, test_tires, test_sensors, test_tboxes,
  test_unit_id_mapping, test_tire_id_mapping, test_sensor_id_mapping, test_tbox_id_mapping;

CREATE TABLE test_units   LIKE units;
INSERT INTO test_units   SELECT * FROM units;

CREATE TABLE test_tires   LIKE tires;
INSERT INTO test_tires   SELECT * FROM tires;

CREATE TABLE test_sensors LIKE sensors;
INSERT INTO test_sensors SELECT * FROM sensors;

CREATE TABLE test_tboxes  LIKE tboxes;
INSERT INTO test_tboxes  SELECT * FROM tboxes;

CREATE TABLE test_unit_id_mapping   LIKE unit_id_mapping;
INSERT INTO test_unit_id_mapping   SELECT * FROM unit_id_mapping;

CREATE TABLE test_tire_id_mapping   LIKE tire_id_mapping;
INSERT INTO test_tire_id_mapping   SELECT * FROM tire_id_mapping;

CREATE TABLE test_sensor_id_mapping LIKE sensor_id_mapping;
INSERT INTO test_sensor_id_mapping SELECT * FROM sensor_id_mapping;

CREATE TABLE test_tbox_id_mapping   LIKE tbox_id_mapping;
INSERT INTO test_tbox_id_mapping   SELECT * FROM tbox_id_mapping;


-- =============================================================================
-- STEP B — Agregar la columna daijin_id  (lo que haríamos en la migración real)
-- =============================================================================
ALTER TABLE test_units   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE test_tires   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE test_sensors ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE test_tboxes  ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;


-- =============================================================================
-- STEP C — Backfill desde el mapping
--   Regla MAX(daijin_id): resuelve los activos con >1 mapping tomando el más reciente.
-- =============================================================================
UPDATE test_units u
  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM test_unit_id_mapping GROUP BY quinta_id) m
    ON m.quinta_id = u.id
  SET u.daijin_id = m.d
  WHERE u.daijin_id IS NULL;

UPDATE test_tires t
  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM test_tire_id_mapping GROUP BY quinta_id) m
    ON m.quinta_id = t.id
  SET t.daijin_id = m.d
  WHERE t.daijin_id IS NULL;

UPDATE test_sensors s
  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM test_sensor_id_mapping GROUP BY quinta_id) m
    ON m.quinta_id = s.id
  SET s.daijin_id = m.d
  WHERE s.daijin_id IS NULL;

UPDATE test_tboxes b
  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM test_tbox_id_mapping GROUP BY quinta_id) m
    ON m.quinta_id = b.id
  SET b.daijin_id = m.d
  WHERE b.daijin_id IS NULL;


-- =============================================================================
-- STEP D — VERIFICACIÓN  (👉 mándame esta salida como feedback)
-- =============================================================================

-- D1. Cobertura: cuántos quedaron con daijin_id vs cuántos esperábamos (distinct mapping)
SELECT 'units'   AS tabla,
       (SELECT COUNT(*) FROM test_units   WHERE daijin_id IS NOT NULL) AS con_id,
       (SELECT COUNT(DISTINCT quinta_id) FROM test_unit_id_mapping)    AS esperado,
       (SELECT COUNT(*) FROM test_units)                              AS total
UNION ALL
SELECT 'tires',
       (SELECT COUNT(*) FROM test_tires   WHERE daijin_id IS NOT NULL),
       (SELECT COUNT(DISTINCT quinta_id) FROM test_tire_id_mapping),
       (SELECT COUNT(*) FROM test_tires)
UNION ALL
SELECT 'sensors',
       (SELECT COUNT(*) FROM test_sensors WHERE daijin_id IS NOT NULL),
       (SELECT COUNT(DISTINCT quinta_id) FROM test_sensor_id_mapping),
       (SELECT COUNT(*) FROM test_sensors)
UNION ALL
SELECT 'tboxes',
       (SELECT COUNT(*) FROM test_tboxes  WHERE daijin_id IS NOT NULL),
       (SELECT COUNT(DISTINCT quinta_id) FROM test_tbox_id_mapping),
       (SELECT COUNT(*) FROM test_tboxes);

-- D2. Integridad: la columna debe coincidir con el mapping. DEBE dar 0 en las 4.
SELECT 'units'   AS tabla, COUNT(*) AS desajustes
  FROM test_units u JOIN test_unit_id_mapping m ON m.quinta_id = u.id
  WHERE u.daijin_id <> m.daijin_id
UNION ALL
SELECT 'tires',  COUNT(*)
  FROM test_tires t JOIN test_tire_id_mapping m ON m.quinta_id = t.id
  WHERE t.daijin_id <> m.daijin_id
UNION ALL
SELECT 'sensors', COUNT(*)
  FROM test_sensors s JOIN test_sensor_id_mapping m ON m.quinta_id = s.id
  WHERE s.daijin_id <> m.daijin_id
UNION ALL
SELECT 'tboxes', COUNT(*)
  FROM test_tboxes b JOIN test_tbox_id_mapping m ON m.quinta_id = b.id
  WHERE b.daijin_id <> m.daijin_id;
-- Nota: el JOIN usa MAX, así que para los ~7 con daijin_id duplicado puede haber
-- "desajuste" esperado si el duplicado no es el MAX. Si D2 > 0, revisamos esos casos.

-- D3. Activos SIN match (daijin_id quedó NULL) — los que irían a 'registering'
SELECT 'units' AS tabla, COUNT(*) AS sin_match FROM test_units   WHERE daijin_id IS NULL
UNION ALL SELECT 'tires',   COUNT(*) FROM test_tires   WHERE daijin_id IS NULL
UNION ALL SELECT 'sensors', COUNT(*) FROM test_sensors WHERE daijin_id IS NULL
UNION ALL SELECT 'tboxes',  COUNT(*) FROM test_tboxes  WHERE daijin_id IS NULL;

-- D4. daijin_id duplicado entre activos distintos (bloquea UNIQUE) — los ~7 casos
SELECT 'tires' AS tabla, daijin_id, COUNT(*) AS veces
  FROM test_tires WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'sensors', daijin_id, COUNT(*)
  FROM test_sensors WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;


-- =============================================================================
-- STEP E — (OPCIONAL) Probar el índice UNIQUE
--   Si quedan daijin_id duplicados (ver D4), este ALTER FALLA a propósito.
--   Es el indicador de que hay que limpiar esos casos antes de exigir unicidad.
-- =============================================================================
-- ALTER TABLE test_tboxes ADD UNIQUE KEY uq_test_tboxes_daijin (daijin_id);  -- limpio, debería pasar
-- ALTER TABLE test_tires  ADD UNIQUE KEY uq_test_tires_daijin  (daijin_id);  -- debería FALLAR si hay dupes


-- =============================================================================
-- STEP F — (OPCIONAL) Triggers de sync bidireccional
--   ⚠️ Requiere log_bin_trust_function_creators = ON (hoy OFF en RDS).
--   Si no se puede, la sincronización viejo↔nuevo se hará como dual-write en el código.
-- =============================================================================
-- DELIMITER //
-- CREATE TRIGGER trg_test_tire_map_to_col AFTER INSERT ON test_tire_id_mapping
--   FOR EACH ROW UPDATE test_tires SET daijin_id = NEW.daijin_id WHERE id = NEW.quinta_id;
-- //
-- DELIMITER ;


-- =============================================================================
-- STEP G — LIMPIEZA  (borra todos los clones; deja la BD como estaba)
-- =============================================================================
DROP TABLE IF EXISTS
  test_units, test_tires, test_sensors, test_tboxes,
  test_unit_id_mapping, test_tire_id_mapping, test_sensor_id_mapping, test_tbox_id_mapping;

-- ============================================================================
--  MIGRACIÓN A PRODUCCIÓN — CRUD_DQ  (enfoque RENAME → legacy)
--
--  Estrategia (decidida por el usuario):
--    - NO tocar las tablas viejas: se les pone prefijo `legacy_`.
--    - Se crean tablas NUEVAS con el NOMBRE CANÓNICO (units, tires, sensors,
--      tboxes) ya con las columnas nuevas (daijin_id, is_deleted, etc.),
--      jalando los datos de las legacy.
--    - Como la nueva conserva el nombre + todas las columnas viejas + las
--      nuevas, TODOS los consumidores (sistema viejo, BEGQL, FE) siguen
--      funcionando automáticamente (la nueva es un superconjunto).
--
--  Ventaja vs ALTER: el corte es un RENAME atómico (rápido), y el rollback es
--  trivial (renombrar legacy_* de vuelta). Los datos viejos quedan intactos.
--
--  ⚠️ REQUISITOS:
--    1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL).
--    2. SNAPSHOT del RDS antes de empezar.
--    3. Ventana de bajo tráfico (el swap final es atómico, pero mejor prevenir).
--    4. Revisar el PASO 0 (FKs/triggers/views) — el enfoque rename NO copia FKs.
--
--  Correr PASO por PASO, revisando las verificaciones antes de avanzar.
--  >>> El swap final (PASO 7) es el punto de no-retorno suave (hay rollback). <<<
-- ============================================================================


-- ############################################################################
--  PASO 0 — PRE-FLIGHT (solo lectura): revisar dependencias que el rename podría
--  romper. `CREATE TABLE ... LIKE` copia índices/PK/auto_increment pero NO copia
--  FOREIGN KEYS. Si algo depende de estas tablas, hay que planearlo antes.
-- ############################################################################

-- ¿Hay FOREIGN KEYS que apunten a units/tires/sensors/tboxes desde otras tablas?
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND REFERENCED_TABLE_NAME IN ('units','tires','sensors','tboxes');

-- ¿Las 4 tablas TIENEN FKs propias hacia otras? (para recrearlas en las _new si aplica)
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('units','tires','sensors','tboxes')
  AND REFERENCED_TABLE_NAME IS NOT NULL;

-- ¿Triggers o vistas que dependan de estas tablas?
SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = DATABASE() AND EVENT_OBJECT_TABLE IN ('units','tires','sensors','tboxes');
SELECT TABLE_NAME FROM information_schema.VIEWS WHERE TABLE_SCHEMA = DATABASE();

-- Tipo real de sensors.company_id (para el MODIFY nullable del PASO 3):
SHOW COLUMNS FROM sensors LIKE 'company_id';

-- >>> Si el PASO 0 arroja FKs entrantes hacia estas tablas, RESOLVER primero:  <<<
-- >>> tras el swap, esas FKs quedarían apuntando a la legacy_*, no a la nueva. <<<


-- ############################################################################
--  PASO 1 — Crear las tablas NUEVAS como copia EXACTA (estructura + índices)
--  y copiar los datos. (`LIKE` no copia FKs; ver PASO 0.)
-- ############################################################################

CREATE TABLE units_new   LIKE units;    INSERT INTO units_new   SELECT * FROM units;
CREATE TABLE tires_new   LIKE tires;    INSERT INTO tires_new   SELECT * FROM tires;
CREATE TABLE sensors_new LIKE sensors;  INSERT INTO sensors_new SELECT * FROM sensors;
CREATE TABLE tboxes_new  LIKE tboxes;   INSERT INTO tboxes_new  SELECT * FROM tboxes;

-- Verificación: conteos orig vs _new DEBEN coincidir (y el AUTO_INCREMENT se
-- ajusta solo tras copiar los ids). NO continuar si no coinciden.
SELECT 'units'   AS tabla, (SELECT COUNT(*) FROM units)   AS orig, (SELECT COUNT(*) FROM units_new)   AS nuevo
UNION ALL SELECT 'tires',   (SELECT COUNT(*) FROM tires),   (SELECT COUNT(*) FROM tires_new)
UNION ALL SELECT 'sensors', (SELECT COUNT(*) FROM sensors), (SELECT COUNT(*) FROM sensors_new)
UNION ALL SELECT 'tboxes',  (SELECT COUNT(*) FROM tboxes),  (SELECT COUNT(*) FROM tboxes_new);


-- ############################################################################
--  PASO 2 — Agregar las columnas nuevas a las _new (daijin_id + is_deleted)
-- ############################################################################

ALTER TABLE units_new   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id, ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tires_new   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id, ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE sensors_new ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id, ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tboxes_new  ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id, ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;


-- ############################################################################
--  PASO 3 — sensors_new.company_id NULLABLE (inventario sin asignar)
--  >>> Usa EXACTAMENTE el tipo que devolvió el SHOW COLUMNS del PASO 0
--      (aquí se asume BIGINT). <<<
-- ############################################################################

ALTER TABLE sensors_new MODIFY COLUMN company_id BIGINT NULL;


-- ############################################################################
--  PASO 4 — Backfill de daijin_id desde las *_id_mapping (que NO se renombran)
--  MAX(daijin_id) por quinta_id. Solo rellena los NULL.
-- ############################################################################

UPDATE units_new   u JOIN (SELECT quinta_id, MAX(daijin_id) d FROM unit_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = u.id SET u.daijin_id = m.d WHERE u.daijin_id IS NULL;
UPDATE tires_new   t JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tire_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = t.id SET t.daijin_id = m.d WHERE t.daijin_id IS NULL;
UPDATE sensors_new s JOIN (SELECT quinta_id, MAX(daijin_id) d FROM sensor_id_mapping GROUP BY quinta_id) m ON m.quinta_id = s.id SET s.daijin_id = m.d WHERE s.daijin_id IS NULL;
UPDATE tboxes_new  b JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tbox_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = b.id SET b.daijin_id = m.d WHERE b.daijin_id IS NULL;


-- ############################################################################
--  PASO 5 — VERIFICAR: sin daijin_id duplicados (cada query debe dar 0 filas).
--  Si alguna devuelve filas, RESOLVER antes de crear el UNIQUE.
-- ############################################################################

SELECT 'units_new'   AS tabla, daijin_id, COUNT(*) c FROM units_new   WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'tires_new'   AS tabla, daijin_id, COUNT(*) c FROM tires_new   WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'sensors_new' AS tabla, daijin_id, COUNT(*) c FROM sensors_new WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'tboxes_new'  AS tabla, daijin_id, COUNT(*) c FROM tboxes_new  WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;


-- ############################################################################
--  PASO 6 — Índice UNIQUE en daijin_id de las _new (MySQL permite múltiples NULL)
--  >>> Solo si el PASO 5 dio 0 filas en las 4. <<<
-- ############################################################################

ALTER TABLE units_new   ADD UNIQUE KEY uq_units_daijin   (daijin_id);
ALTER TABLE tires_new   ADD UNIQUE KEY uq_tires_daijin   (daijin_id);
ALTER TABLE sensors_new ADD UNIQUE KEY uq_sensors_daijin (daijin_id);
ALTER TABLE tboxes_new  ADD UNIQUE KEY uq_tboxes_daijin  (daijin_id);

-- Verificación de cobertura antes del swap (con_daijin vs total):
SELECT 'units'   AS tabla, COUNT(*) total, COUNT(daijin_id) con_daijin FROM units_new
UNION ALL SELECT 'tires',   COUNT(*), COUNT(daijin_id) FROM tires_new
UNION ALL SELECT 'sensors', COUNT(*), COUNT(daijin_id) FROM sensors_new
UNION ALL SELECT 'tboxes',  COUNT(*), COUNT(daijin_id) FROM tboxes_new;


-- ############################################################################
--  PASO 7 — SWAP ATÓMICO (el corte). Un solo RENAME: las viejas pasan a
--  legacy_* y las _new toman el nombre canónico. Es instantáneo y atómico:
--  no hay momento en que `units` no exista.
--  >>> Punto de corte. A partir de aquí, TODOS usan las nuevas. <<<
-- ############################################################################

RENAME TABLE
  units   TO legacy_units,   units_new   TO units,
  tires   TO legacy_tires,   tires_new   TO tires,
  sensors TO legacy_sensors, sensors_new TO sensors,
  tboxes  TO legacy_tboxes,  tboxes_new  TO tboxes;


-- ############################################################################
--  PASO 8 — VERIFICACIÓN POST-SWAP
-- ############################################################################

-- Las nuevas ya son las canónicas y traen daijin_id + is_deleted + el índice:
-- SHOW COLUMNS FROM units;   SHOW INDEX FROM units WHERE Key_name LIKE '%daijin%';
SELECT 'units'   AS tabla, COUNT(*) total, COUNT(daijin_id) con_daijin, SUM(is_deleted) borrados FROM units
UNION ALL SELECT 'tires',   COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM tires
UNION ALL SELECT 'sensors', COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM sensors
UNION ALL SELECT 'tboxes',  COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM tboxes;

-- Las viejas quedaron intactas bajo legacy_* (red de seguridad):
-- SELECT COUNT(*) FROM legacy_units;  -- etc.


-- ############################################################################
--  ROLLBACK — si algo sale mal DESPUÉS del swap: revertir el nombre.
--  ⚠️ Las escrituras hechas sobre las nuevas DESPUÉS del swap se PIERDEN al
--     volver a la legacy. Hacerlo solo si se detecta el problema pronto.
-- ############################################################################
--
--   RENAME TABLE
--     units   TO units_new,   legacy_units   TO units,
--     tires   TO tires_new,   legacy_tires   TO tires,
--     sensors TO sensors_new, legacy_sensors TO sensors,
--     tboxes  TO tboxes_new,  legacy_tboxes  TO tboxes;
--
--  (Antes del PASO 7 el rollback es simplemente DROP de las _new, sin tocar nada.)
--
--
--  LIMPIEZA (cuando todo esté estable, tras varios días de operación real):
--    DROP TABLE legacy_units, legacy_tires, legacy_sensors, legacy_tboxes;
-- ============================================================================

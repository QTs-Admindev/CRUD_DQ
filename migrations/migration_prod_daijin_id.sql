-- ============================================================================
--  MIGRACIÓN A PRODUCCIÓN — CRUD_DQ
--  Objetivo: columna `daijin_id` + `is_deleted` en las tablas REALES
--            (units, tires, sensors, tboxes), backfill desde las *_id_mapping,
--            e índice UNIQUE en daijin_id.
--
--  ⚠️ REQUISITOS ANTES DE EMPEZAR:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL).
--   2. SNAPSHOT del RDS desde la consola de AWS (además del backup de abajo).
--   3. Correr en ventana de bajo tráfico.
--
--  ✅ Es una migración ADITIVA: agrega columnas NULLABLE y un índice. NO borra
--     nada ni toca las *_id_mapping, así que el sistema viejo y BEGQL siguen
--     funcionando en paralelo (coexistencia segura). El retiro de las mapping
--     es un paso POSTERIOR y separado (no está aquí).
--
--  ⚠️ OJO MySQL: cada ALTER/DDL hace auto-commit (no hay rollback transaccional
--     de un ALTER). Por eso el BACKUP del PASO 0 es la red de seguridad real.
--
--  Correr PASO por PASO, revisando las verificaciones antes de avanzar.
-- ============================================================================


-- ############################################################################
--  PASO 0 — BACKUP  (correr ANTES de todo)
--  Copia estructura + datos de cada tabla a una *_bak_20260704.
--  Si algo sale mal, se restaura desde estas copias (ver ROLLBACK al final).
--  >>> Si corres en otra fecha, cambia el sufijo _20260704 en TODO el archivo. <<<
-- ############################################################################

CREATE TABLE units_bak_20260704   LIKE units;    INSERT INTO units_bak_20260704   SELECT * FROM units;
CREATE TABLE tires_bak_20260704   LIKE tires;    INSERT INTO tires_bak_20260704   SELECT * FROM tires;
CREATE TABLE sensors_bak_20260704 LIKE sensors;  INSERT INTO sensors_bak_20260704 SELECT * FROM sensors;
CREATE TABLE tboxes_bak_20260704  LIKE tboxes;   INSERT INTO tboxes_bak_20260704  SELECT * FROM tboxes;

-- Backup también de las mapping (fuente del daijin_id que vamos a copiar):
CREATE TABLE unit_id_mapping_bak_20260704   LIKE unit_id_mapping;   INSERT INTO unit_id_mapping_bak_20260704   SELECT * FROM unit_id_mapping;
CREATE TABLE tire_id_mapping_bak_20260704   LIKE tire_id_mapping;   INSERT INTO tire_id_mapping_bak_20260704   SELECT * FROM tire_id_mapping;
CREATE TABLE sensor_id_mapping_bak_20260704 LIKE sensor_id_mapping; INSERT INTO sensor_id_mapping_bak_20260704 SELECT * FROM sensor_id_mapping;
CREATE TABLE tbox_id_mapping_bak_20260704   LIKE tbox_id_mapping;   INSERT INTO tbox_id_mapping_bak_20260704   SELECT * FROM tbox_id_mapping;

-- Verificación del backup: los conteos orig vs bak DEBEN coincidir.
SELECT 'units'   AS tabla, (SELECT COUNT(*) FROM units)   AS orig, (SELECT COUNT(*) FROM units_bak_20260704)   AS bak
UNION ALL SELECT 'tires',   (SELECT COUNT(*) FROM tires),   (SELECT COUNT(*) FROM tires_bak_20260704)
UNION ALL SELECT 'sensors', (SELECT COUNT(*) FROM sensors), (SELECT COUNT(*) FROM sensors_bak_20260704)
UNION ALL SELECT 'tboxes',  (SELECT COUNT(*) FROM tboxes),  (SELECT COUNT(*) FROM tboxes_bak_20260704);
-- >>> NO CONTINUAR si algún conteo no coincide. <<<


-- ############################################################################
--  PASO 1 — Columna daijin_id  (Cambio 1)
--  VARCHAR(50) NULL: un activo en 'registering' aún no tiene daijin_id.
-- ############################################################################

ALTER TABLE units   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE tires   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE sensors ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE tboxes  ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;


-- ############################################################################
--  PASO 2 — Columna is_deleted (soft delete)  (Cambio 5)
--  TINYINT(1) NOT NULL DEFAULT 0: las filas existentes quedan como NO borradas.
-- ############################################################################

ALTER TABLE units   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tires   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE sensors ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tboxes  ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;


-- ############################################################################
--  PASO 3 — sensors.company_id NULLABLE (inventario sin asignar)  (Cambio 8)
--  Los sensores se registran sin compañía (NULL) y se asignan después.
--  >>> Antes de correrlo, confirma el tipo real de la columna: <<<
--        SHOW COLUMNS FROM sensors LIKE 'company_id';
--  y usa EXACTAMENTE ese tipo en el MODIFY (aquí se asume BIGINT).
-- ############################################################################

ALTER TABLE sensors MODIFY COLUMN company_id BIGINT NULL;


-- ############################################################################
--  PASO 4 — Backfill de daijin_id desde las *_id_mapping  (Cambio 2)
--  MAX(daijin_id) por quinta_id para los activos con >1 mapping.
--  Solo rellena los que están en NULL (idempotente si se re-corre).
-- ############################################################################

UPDATE units u   JOIN (SELECT quinta_id, MAX(daijin_id) d FROM unit_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = u.id SET u.daijin_id = m.d WHERE u.daijin_id IS NULL;
UPDATE tires t   JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tire_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = t.id SET t.daijin_id = m.d WHERE t.daijin_id IS NULL;
UPDATE sensors s JOIN (SELECT quinta_id, MAX(daijin_id) d FROM sensor_id_mapping GROUP BY quinta_id) m ON m.quinta_id = s.id SET s.daijin_id = m.d WHERE s.daijin_id IS NULL;
UPDATE tboxes b  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tbox_id_mapping   GROUP BY quinta_id) m ON m.quinta_id = b.id SET b.daijin_id = m.d WHERE b.daijin_id IS NULL;


-- ############################################################################
--  PASO 5 — VERIFICAR antes del UNIQUE: NO debe haber daijin_id duplicados.
--  Cada query debe devolver 0 filas. Si alguna devuelve filas, RESOLVER antes
--  de crear el índice (el ensayo dio 0 duplicados, pero verificar en real).
-- ############################################################################

SELECT 'units'   AS tabla, daijin_id, COUNT(*) c FROM units   WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'tires'   AS tabla, daijin_id, COUNT(*) c FROM tires   WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'sensors' AS tabla, daijin_id, COUNT(*) c FROM sensors WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;
SELECT 'tboxes'  AS tabla, daijin_id, COUNT(*) c FROM tboxes  WHERE daijin_id IS NOT NULL GROUP BY daijin_id HAVING COUNT(*) > 1;


-- ############################################################################
--  PASO 6 — Índice UNIQUE en daijin_id  (Cambio 3)
--  MySQL permite múltiples NULL en UNIQUE -> no afecta a los 'registering'.
--  Impide que dos activos locales reclamen el mismo daijin_id.
--  >>> Solo correr si el PASO 5 dio 0 filas en las 4 tablas. <<<
-- ############################################################################

ALTER TABLE units   ADD UNIQUE KEY uq_units_daijin   (daijin_id);
ALTER TABLE tires   ADD UNIQUE KEY uq_tires_daijin   (daijin_id);
ALTER TABLE sensors ADD UNIQUE KEY uq_sensors_daijin (daijin_id);
ALTER TABLE tboxes  ADD UNIQUE KEY uq_tboxes_daijin  (daijin_id);


-- ############################################################################
--  PASO 7 — VERIFICACIÓN FINAL
--  Cobertura del backfill (cuántos quedaron con daijin_id vs total) y que las
--  columnas/índice existen.
-- ############################################################################

SELECT 'units'   AS tabla, COUNT(*) total, COUNT(daijin_id) con_daijin, SUM(is_deleted) borrados FROM units
UNION ALL SELECT 'tires',   COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM tires
UNION ALL SELECT 'sensors', COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM sensors
UNION ALL SELECT 'tboxes',  COUNT(*), COUNT(daijin_id), SUM(is_deleted) FROM tboxes;

-- Estructura (deben aparecer daijin_id, is_deleted y el índice uq_*_daijin):
-- SHOW COLUMNS FROM units;   SHOW INDEX FROM units WHERE Key_name LIKE '%daijin%';
-- (repetir por tabla)


-- ############################################################################
--  (OPCIONAL) PASO 8 — Folio único por compañía  (Cambio 7)
--  La regla ya está en el CÓDIGO; este constraint es defensa a nivel BD.
--  Solo si el sistema NO necesita repetir folio con distinto prefix en la misma
--  compañía. Verificar primero que no haya conflictos:
--    SELECT folio, company_id, COUNT(*) FROM tires GROUP BY folio, company_id HAVING COUNT(*)>1;
--  Si da 0 filas:
--    ALTER TABLE tires DROP INDEX prefix;                          -- el UNIQUE actual (prefix,folio,company_id)
--    ALTER TABLE tires ADD UNIQUE KEY uq_tires_folio_company (folio, company_id);


-- ############################################################################
--  ROLLBACK — si algo sale mal
-- ############################################################################
--
--  OPCIÓN A (preferida) — revertir solo lo que agregamos (conserva escrituras
--  posteriores). Correr en orden inverso, dropeando primero el índice:
--
--    ALTER TABLE units   DROP INDEX uq_units_daijin;
--    ALTER TABLE tires   DROP INDEX uq_tires_daijin;
--    ALTER TABLE sensors DROP INDEX uq_sensors_daijin;
--    ALTER TABLE tboxes  DROP INDEX uq_tboxes_daijin;
--    ALTER TABLE units   DROP COLUMN daijin_id, DROP COLUMN is_deleted;
--    ALTER TABLE tires   DROP COLUMN daijin_id, DROP COLUMN is_deleted;
--    ALTER TABLE sensors DROP COLUMN daijin_id, DROP COLUMN is_deleted;
--    ALTER TABLE tboxes  DROP COLUMN daijin_id, DROP COLUMN is_deleted;
--    -- (sensors.company_id se puede dejar NULLABLE; es aditivo e inofensivo)
--
--  OPCIÓN B (último recurso) — restaurar la tabla COMPLETA desde el backup.
--  ⚠️ PIERDE cualquier escritura hecha DESPUÉS del backup. Solo si la Opción A
--     no basta y aún no hubo tráfico nuevo:
--
--    DROP TABLE units;   RENAME TABLE units_bak_20260704   TO units;
--    DROP TABLE tires;   RENAME TABLE tires_bak_20260704   TO tires;
--    DROP TABLE sensors; RENAME TABLE sensors_bak_20260704 TO sensors;
--    DROP TABLE tboxes;  RENAME TABLE tboxes_bak_20260704  TO tboxes;
--
--
--  LIMPIEZA (cuando la migración esté validada y estable, tras varios días):
--    DROP TABLE units_bak_20260704, tires_bak_20260704, sensors_bak_20260704, tboxes_bak_20260704;
--    DROP TABLE unit_id_mapping_bak_20260704, tire_id_mapping_bak_20260704,
--               sensor_id_mapping_bak_20260704, tbox_id_mapping_bak_20260704;
-- ============================================================================

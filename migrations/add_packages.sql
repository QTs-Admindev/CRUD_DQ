-- ============================================================================
--  Migración: Feature "Paquetes de hardware"
--
--  Un "paquete" es un kit de electrónica (1 TBox + N sensores, SIN llantas)
--  preparado en la compañía admin (company_id=2) y atado a un unit_catalog.
--  Luego se mueve a otra compañía y al asignarlo a una unidad real se crean/
--  reutilizan las llantas y se monta todo.
--
--  Esta migración es ADITIVA:
--    - CREATE TABLE packages (tabla nueva, no toca nada existente).
--    - ADD COLUMN package_id (NULLABLE) en tboxes y sensors: las filas
--      existentes quedan con package_id NULL (no pertenecen a ningún paquete),
--      así el sistema actual sigue funcionando igual (coexistencia segura).
--
--  ⚠️ REQUISITOS ANTES DE EMPEZAR:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL, igual que las otras
--      migraciones de esta carpeta). Correr en DBeaver con el usuario master.
--   2. SNAPSHOT del RDS desde la consola de AWS (además del backup de abajo).
--   3. Correr en ventana de bajo tráfico.
--
--  ⚠️ OJO MySQL: cada ALTER/DDL hace auto-commit (no hay rollback transaccional
--     de un ALTER). Por eso el BACKUP del PASO 0 es la red de seguridad real.
--
--  Correr PASO por PASO, revisando las verificaciones antes de avanzar.
-- ============================================================================


-- ############################################################################
--  PASO 0 — BACKUP  (correr ANTES de todo)
--  Copia estructura + datos de las tablas que vamos a alterar.
--  >>> Si corres en otra fecha, cambia el sufijo _20260724 en TODO el archivo. <<<
-- ############################################################################

CREATE TABLE tboxes_bak_20260724  LIKE tboxes;   INSERT INTO tboxes_bak_20260724  SELECT * FROM tboxes;
CREATE TABLE sensors_bak_20260724 LIKE sensors;  INSERT INTO sensors_bak_20260724 SELECT * FROM sensors;

-- Verificación del backup: los conteos orig vs bak DEBEN coincidir.
SELECT 'tboxes'  AS tabla, (SELECT COUNT(*) FROM tboxes)  AS orig, (SELECT COUNT(*) FROM tboxes_bak_20260724)  AS bak
UNION ALL SELECT 'sensors', (SELECT COUNT(*) FROM sensors), (SELECT COUNT(*) FROM sensors_bak_20260724);
-- >>> NO CONTINUAR si algún conteo no coincide. <<<


-- ############################################################################
--  PASO 1 — Tabla nueva `packages`
--  status: prepared (kit armado en company 2) -> assigned (montado en una unidad)
--          -> retired (dado de baja). Timestamps en epoch ms (como el resto de la BD).
-- ############################################################################

CREATE TABLE packages (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name            VARCHAR(255)    NULL,
  unit_catalog_id INT             NOT NULL,
  company_id      INT             NOT NULL,
  unit_id         INT             NULL,
  status          ENUM('prepared','assigned','retired') NOT NULL DEFAULT 'prepared',
  created_at      BIGINT          NULL,
  updated_at      BIGINT          NULL,
  PRIMARY KEY (id),
  KEY idx_packages_company (company_id),
  KEY idx_packages_status  (status),
  KEY idx_packages_unit    (unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ############################################################################
--  PASO 2 — Columna package_id en tboxes y sensors
--  BIGINT UNSIGNED NULL para casar con packages.id. NULL = no pertenece a un
--  paquete (todo el inventario existente queda así).
-- ############################################################################

ALTER TABLE tboxes  ADD COLUMN package_id BIGINT UNSIGNED NULL;
ALTER TABLE sensors ADD COLUMN package_id BIGINT UNSIGNED NULL;

-- Índices para buscar los miembros de un paquete (move/assign hacen WHERE package_id = ?).
ALTER TABLE tboxes  ADD KEY idx_tboxes_package  (package_id);
ALTER TABLE sensors ADD KEY idx_sensors_package (package_id);


-- ############################################################################
--  PASO 3 — VERIFICACIÓN FINAL
-- ############################################################################

-- La tabla packages debe existir y estar vacía:
SELECT COUNT(*) AS packages_rows FROM packages;

-- Las columnas package_id deben existir (y estar todas en NULL de arranque):
SELECT 'tboxes'  AS tabla, COUNT(*) total, COUNT(package_id) con_package FROM tboxes
UNION ALL SELECT 'sensors', COUNT(*), COUNT(package_id) FROM sensors;
-- >>> con_package debe ser 0 en ambas justo después de migrar. <<<

-- Estructura (opcional):
-- SHOW COLUMNS FROM packages;
-- SHOW COLUMNS FROM tboxes  LIKE 'package_id';
-- SHOW COLUMNS FROM sensors LIKE 'package_id';


-- ############################################################################
--  ROLLBACK — si algo sale mal
-- ############################################################################
--
--  OPCIÓN A (preferida) — revertir solo lo que agregamos:
--
--    ALTER TABLE tboxes  DROP INDEX idx_tboxes_package;
--    ALTER TABLE sensors DROP INDEX idx_sensors_package;
--    ALTER TABLE tboxes  DROP COLUMN package_id;
--    ALTER TABLE sensors DROP COLUMN package_id;
--    DROP TABLE packages;
--
--  OPCIÓN B (último recurso) — restaurar las tablas COMPLETAS desde el backup.
--  ⚠️ PIERDE cualquier escritura hecha DESPUÉS del backup. Solo si la Opción A
--     no basta y aún no hubo tráfico nuevo:
--
--    DROP TABLE tboxes;  RENAME TABLE tboxes_bak_20260724  TO tboxes;
--    DROP TABLE sensors; RENAME TABLE sensors_bak_20260724 TO sensors;
--    DROP TABLE packages;
--
--
--  LIMPIEZA (cuando la migración esté validada y estable, tras varios días):
--    DROP TABLE tboxes_bak_20260724, sensors_bak_20260724;
-- ============================================================================

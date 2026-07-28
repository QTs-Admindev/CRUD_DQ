-- ============================================================================
--  MIGRACIÓN COMPLETA — Feature "Paquetes de hardware" (kits)
--  Une en un solo archivo:  add_packages.sql  +  add_sensor_mount_position.sql
--
--  Un "paquete" = kit de electrónica (1 TBox + N sensores, SIN llantas) preparado
--  en la compañía admin (company_id=2) y atado a un unit_catalog. Se mueve a otra
--  compañía y al asignarlo a una unidad real se crean/reutilizan las llantas y se
--  monta todo. El `mount_position` de cada sensor hace el mapeo sensor->posición
--  determinista (no depende del orden de id).
--
--  Esta migración es ADITIVA (coexistencia segura con el sistema actual):
--    - CREATE TABLE packages                       (tabla nueva)
--    - ADD COLUMN package_id      en tboxes y sensors   (NULLABLE)
--    - ADD COLUMN mount_position  en sensors            (NULLABLE)
--  Las filas existentes quedan con esas columnas en NULL; nada deja de funcionar.
--
--  ⚠️ REQUISITOS ANTES DE EMPEZAR:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene permisos DDL).
--      Correr en DBeaver con el usuario master.
--   2. SNAPSHOT del RDS desde la consola de AWS (además del backup del PASO 0).
--   3. Correr en ventana de bajo tráfico.
--
--  ⚠️ OJO MySQL: cada ALTER/DDL hace auto-commit (no hay rollback transaccional
--     de un ALTER). Por eso el BACKUP del PASO 0 es la red de seguridad real.
--
--  ⚠️ Si corres en OTRA fecha, cambia el sufijo _20260728 en TODO el archivo.
--
--  Correr PASO por PASO, revisando cada verificación antes de avanzar.
-- ============================================================================


-- ############################################################################
--  PASO 0 — BACKUP  (correr ANTES de todo)
--  Copia estructura + datos de las tablas que vamos a alterar (tboxes, sensors).
-- ############################################################################

CREATE TABLE tboxes_bak_20260728  LIKE tboxes;   INSERT INTO tboxes_bak_20260728  SELECT * FROM tboxes;
CREATE TABLE sensors_bak_20260728 LIKE sensors;  INSERT INTO sensors_bak_20260728 SELECT * FROM sensors;

-- Verificación del backup: los conteos orig vs bak DEBEN coincidir.
SELECT 'tboxes'  AS tabla, (SELECT COUNT(*) FROM tboxes)  AS orig, (SELECT COUNT(*) FROM tboxes_bak_20260728)  AS bak
UNION ALL SELECT 'sensors', (SELECT COUNT(*) FROM sensors), (SELECT COUNT(*) FROM sensors_bak_20260728);
-- >>> NO CONTINUAR si algún conteo no coincide. <<<


-- ############################################################################
--  PASO 1 — Tabla nueva `packages`
--  status: prepared (kit armado en company 2) -> assigned (montado en una unidad)
--          -> retired (baja). Timestamps en epoch ms (como el resto de la BD).
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
--  PASO 3 — Columna mount_position en sensors  (posición determinista del kit)
--  Al armar el paquete, el sensor i-ésimo (orden de captura del FE = orden de
--  posición) guarda su mount_position 1-based. El assign mapea por esta columna;
--  si un paquete viejo la tiene en NULL, el assign cae al orden id ASC.
-- ############################################################################

ALTER TABLE sensors ADD COLUMN mount_position INT NULL;
ALTER TABLE sensors ADD KEY idx_sensors_mount_position (mount_position);


-- ############################################################################
--  PASO 4 — VERIFICACIÓN FINAL
-- ############################################################################

-- La tabla packages debe existir y estar vacía:
SELECT COUNT(*) AS packages_rows FROM packages;

-- Las columnas nuevas deben existir y estar TODAS en NULL de arranque:
SELECT 'tboxes'  AS tabla, COUNT(*) AS total, COUNT(package_id) AS con_package, NULL AS con_pos FROM tboxes
UNION ALL
SELECT 'sensors', COUNT(*), COUNT(package_id), COUNT(mount_position) FROM sensors;
-- >>> con_package debe ser 0 en ambas, y con_pos debe ser 0 en sensors. <<<

-- Estructura (opcional):
-- SHOW COLUMNS FROM packages;
-- SHOW COLUMNS FROM tboxes  LIKE 'package_id';
-- SHOW COLUMNS FROM sensors LIKE 'package_id';
-- SHOW COLUMNS FROM sensors LIKE 'mount_position';


-- ############################################################################
--  ROLLBACK — si algo sale mal
-- ############################################################################
--
--  OPCIÓN A (preferida) — revertir solo lo que agregamos:
--
--    ALTER TABLE sensors DROP INDEX idx_sensors_mount_position;
--    ALTER TABLE sensors DROP COLUMN mount_position;
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
--    DROP TABLE tboxes;  RENAME TABLE tboxes_bak_20260728  TO tboxes;
--    DROP TABLE sensors; RENAME TABLE sensors_bak_20260728 TO sensors;
--    DROP TABLE packages;
--
--
--  LIMPIEZA (cuando la migración esté validada y estable, tras varios días):
--    DROP TABLE tboxes_bak_20260728, sensors_bak_20260728;
-- ============================================================================

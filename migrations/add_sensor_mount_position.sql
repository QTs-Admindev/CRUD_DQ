-- ============================================================================
--  MIGRACIÓN — sensors.mount_position  (para paquetes: posición determinista)
--
--  Agrega una columna NULLABLE `mount_position` a `sensors`. Al armar un paquete,
--  el sensor i-ésimo (en orden de captura del FE = orden de posición) guarda su
--  mount_position 1-based. El assign del paquete mapea sensor -> posición por esta
--  columna, en vez de depender del orden de id (que puede diferir cuando un
--  sensorCode ya existía y se reutiliza su id viejo).
--
--    - ADD COLUMN mount_position (NULLABLE): las filas existentes quedan en NULL
--      (sensores sueltos / paquetes viejos). El assign cae al orden id ASC cuando
--      no todas las posiciones tienen mount_position -> coexistencia segura.
--
--  ⚠️ REQUISITOS:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL).
--   2. SNAPSHOT del RDS antes (además del backup de abajo).
--   3. Ventana de bajo tráfico. Cada ALTER hace auto-commit (sin rollback txn).
-- ============================================================================

-- PASO 0 — BACKUP (correr ANTES). Cambia el sufijo _20260727 si corres en otra fecha.
CREATE TABLE sensors_bak_mp_20260727 LIKE sensors;
INSERT INTO sensors_bak_mp_20260727 SELECT * FROM sensors;

-- PASO 1 — Columna nueva (NULLABLE) + índice para buscarla en el assign.
ALTER TABLE sensors ADD COLUMN mount_position INT NULL;
ALTER TABLE sensors ADD KEY idx_sensors_mount_position (mount_position);

-- PASO 2 — Verificación (debe dar con_pos = 0 justo después de migrar).
SELECT COUNT(*) AS total, COUNT(mount_position) AS con_pos FROM sensors;
-- SHOW COLUMNS FROM sensors LIKE 'mount_position';

-- ROLLBACK (si hiciera falta):
--   ALTER TABLE sensors DROP INDEX idx_sensors_mount_position;
--   ALTER TABLE sensors DROP COLUMN mount_position;

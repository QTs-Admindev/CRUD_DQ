-- ============================================================================
--  MIGRACIÓN — sensors.batch_code  (lote al que pertenece cada sensor)
--
--  Al subir sensores por Excel, quien sube escribe el identificador del lote
--  (el que trae el envío del proveedor). Se guarda en cada sensor para poder
--  responder "¿de qué lote vino este sensor?" y "¿qué sensores llegaron en el
--  lote X?".
--
--    - ADD COLUMN batch_code VARCHAR(64) NULL: los sensores existentes quedan en
--      NULL (llegaron antes de que existiera el campo). Nada se rellena.
--    - Índice para filtrar por lote (GET /list/sensors?batch_code=...).
--
--  ⚠️ ORDEN DE DESPLIEGUE (no es opcional):
--    1. Esta migración.
--    2. El backend (CRUD_DQ), que ya escribe y lista la columna.
--    3. El FE.
--  Desplegar el backend ANTES de correr esto rompe el listado y la carga masiva
--  de sensores: los dos nombran una columna que todavía no existe.
--
--  ⚠️ REQUISITOS:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL).
--   2. SNAPSHOT del RDS antes.
--   3. Ventana de bajo tráfico. Cada ALTER hace auto-commit (sin rollback txn).
-- ============================================================================

-- PASO 0 — Foto de antes (solo lectura). La columna NO debe existir todavía.
SELECT COUNT(*) AS total_sensores FROM sensors;
SHOW COLUMNS FROM sensors LIKE 'batch_code';   -- debe salir vacío

-- PASO 1 — Columna nueva (NULLABLE) + índice.
ALTER TABLE sensors ADD COLUMN batch_code VARCHAR(64) NULL;
ALTER TABLE sensors ADD KEY idx_sensors_batch_code (batch_code);

-- PASO 2 — Verificación: la columna existe y todas las filas quedan en NULL.
SHOW COLUMNS FROM sensors LIKE 'batch_code';
SELECT COUNT(*) AS total, COUNT(batch_code) AS con_lote FROM sensors;   -- con_lote = 0

-- ROLLBACK (si hiciera falta; primero revertir backend y FE):
--   ALTER TABLE sensors DROP INDEX idx_sensors_batch_code;
--   ALTER TABLE sensors DROP COLUMN batch_code;

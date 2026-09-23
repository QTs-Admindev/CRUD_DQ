-- ============================================================================
--  MIGRACIÓN — tboxes.batch_code  (lote al que pertenece cada Qbox)
--
--  Al subir Qbox (por Excel o uno por uno), quien sube escribe el identificador del lote
--  (el que trae el envío del proveedor). Se guarda en cada Qbox para poder
--  responder "¿de qué lote vino este Qbox?" y "¿qué Qbox llegaron en el
--  lote X?".
--
--    - ADD COLUMN batch_code VARCHAR(64) NULL: los Qbox existentes quedan en
--      NULL (llegaron antes de que existiera el campo). Nada se rellena.
--    - Índice para filtrar por lote (GET /list/tboxes?batch_code=...).
--
--  ⚠️ ORDEN DE DESPLIEGUE (no es opcional):
--    1. Esta migración.
--    2. El backend (CRUD_DQ), que ya escribe y lista la columna.
--    3. El FE.
--  Desplegar el backend ANTES de correr esto rompe el listado, el alta y la carga masiva
--  de Qbox: los dos nombran una columna que todavía no existe.
--
--  ⚠️ REQUISITOS:
--   1. Usuario ADMIN/MASTER del RDS (app_user NO tiene DDL).
--   2. SNAPSHOT del RDS antes.
--   3. Ventana de bajo tráfico. Cada ALTER hace auto-commit (sin rollback txn).
-- ============================================================================

-- PASO 0 — Foto de antes (solo lectura). La columna NO debe existir todavía.
SELECT COUNT(*) AS total_qbox FROM tboxes;
SHOW COLUMNS FROM tboxes LIKE 'batch_code';   -- debe salir vacío

-- PASO 1 — Columna nueva (NULLABLE) + índice.
ALTER TABLE tboxes ADD COLUMN batch_code VARCHAR(64) NULL;
ALTER TABLE tboxes ADD KEY idx_tboxes_batch_code (batch_code);

-- PASO 2 — Verificación: la columna existe y todas las filas quedan en NULL.
SHOW COLUMNS FROM tboxes LIKE 'batch_code';
SELECT COUNT(*) AS total, COUNT(batch_code) AS con_lote FROM tboxes;   -- con_lote = 0

-- ROLLBACK (si hiciera falta; primero revertir backend y FE):
--   ALTER TABLE tboxes DROP INDEX idx_tboxes_batch_code;
--   ALTER TABLE tboxes DROP COLUMN batch_code;

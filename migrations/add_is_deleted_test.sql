-- =============================================================================
-- Agregar columna is_deleted (soft delete) a las tablas test_
-- Correr con el usuario MASTER/admin del RDS (app_user no tiene ALTER).
-- =============================================================================
ALTER TABLE test_units   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE test_tires   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE test_sensors ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE test_tboxes  ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;

-- Verificación
SELECT 'test_units' t,   COUNT(*) total, SUM(is_deleted) borrados FROM test_units
UNION ALL SELECT 'test_tires',   COUNT(*), SUM(is_deleted) FROM test_tires
UNION ALL SELECT 'test_sensors', COUNT(*), SUM(is_deleted) FROM test_sensors
UNION ALL SELECT 'test_tboxes',  COUNT(*), SUM(is_deleted) FROM test_tboxes;

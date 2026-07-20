-- =============================================================================
-- Make company_id nullable on test_sensors.
-- Sensors are registered into inventory WITHOUT a company (company_id = NULL);
-- the company is set later via a separate "assign" operation.
-- (test_tboxes.company_id is already nullable.)
-- Run with the RDS MASTER/admin user (app_user has no ALTER).
-- =============================================================================
ALTER TABLE test_sensors MODIFY COLUMN company_id BIGINT NULL;

-- verify
SHOW COLUMNS FROM test_sensors LIKE 'company_id';

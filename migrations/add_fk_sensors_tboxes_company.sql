-- ============================================================================
--  Migración: FK company_id -> companies en sensors y tboxes (como units/tires)
--
--  Contexto: units y tires ya tienen FK company_id -> companies. sensors y
--  tboxes NO la tenían (company_id era BIGINT NULL sin constraint), así que no
--  había validación a nivel BD de que la compañía exista.
--
--  Pre-checks (2026-07-06, ya verificados en read-only):
--    - Sin huérfanos: todo company_id NOT NULL existe en companies.
--    - max(company_id) = 133  ->  cabe en INT (el bigint->int no trunca).
--    - Tablas InnoDB.
--    - companies.id es INT  ->  hay que igualar el tipo antes del FK.
--
--  Requiere usuario con DDL (app_user NO tiene DDL). Correr en DBeaver con el
--  usuario admin/master del RDS. Idempotente-ish: si ya existe el FK, el ADD falla.
-- ============================================================================

-- PASO 1 — igualar el tipo a INT (para que el FK case con companies.id).
--          NULL se mantiene (inventario sin compañía sigue permitido).
ALTER TABLE sensors MODIFY COLUMN company_id INT NULL;
ALTER TABLE tboxes  MODIFY COLUMN company_id INT NULL;

-- PASO 2 — crear el FK. RESTRICT por defecto: no deja borrar una compañía con
--          sensores/tboxes asignados (igual que la integridad de units/tires).
ALTER TABLE sensors
  ADD CONSTRAINT fk_sensors_company FOREIGN KEY (company_id) REFERENCES companies(id);
ALTER TABLE tboxes
  ADD CONSTRAINT fk_tboxes_company  FOREIGN KEY (company_id) REFERENCES companies(id);

-- VERIFICACIÓN — deben aparecer las 4 FKs (units, tires, sensors, tboxes).
SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE()
  AND COLUMN_NAME = 'company_id'
  AND REFERENCED_TABLE_NAME = 'companies'
ORDER BY TABLE_NAME;

-- ROLLBACK (si hiciera falta):
--   ALTER TABLE sensors DROP FOREIGN KEY fk_sensors_company;
--   ALTER TABLE tboxes  DROP FOREIGN KEY fk_tboxes_company;
--   (y opcionalmente volver company_id a BIGINT, aunque INT es lo correcto.)

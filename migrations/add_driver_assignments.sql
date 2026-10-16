-- Operador asignado a cada unidad, con historial. La fila vigente es la que tiene
-- released_at NULL; las columnas generadas impiden dos vigentes por unidad o por operador.
-- Correr con el usuario master ANTES de desplegar el código.


CREATE TABLE driver_assignments (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  company_id        INT             NOT NULL,
  unit_id           BIGINT          NOT NULL,
  driver_id         BIGINT          NOT NULL,
  assigned_at       BIGINT          NOT NULL,
  assigned_by       VARCHAR(255)    NULL,
  released_at       BIGINT          NULL,
  released_by       VARCHAR(255)    NULL,
  active_unit_id    BIGINT GENERATED ALWAYS AS (CASE WHEN released_at IS NULL THEN unit_id END) STORED,
  active_driver_id  BIGINT GENERATED ALWAYS AS (CASE WHEN released_at IS NULL THEN driver_id END) STORED,
  PRIMARY KEY (id),
  UNIQUE KEY uq_da_active_unit (active_unit_id),
  UNIQUE KEY uq_da_active_driver (active_driver_id),
  KEY idx_da_unit (unit_id, assigned_at),
  KEY idx_da_driver (driver_id, assigned_at),
  KEY idx_da_company (company_id),
  CONSTRAINT fk_da_company FOREIGN KEY (company_id) REFERENCES companies (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Verificación: debe salir la tabla con sus 10 columnas.
SHOW COLUMNS FROM driver_assignments;


-- Rollback:
--   DROP TABLE driver_assignments;

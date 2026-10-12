-- Sedes y almacenes por compañía. Empieza en limpio: se borran los almacenes de
-- Quinta 1 (`warehouses`, `warehouse_tires`) y se crea `warehouses` nueva.
-- Correr con el usuario master, con snapshot previo.
-- Si ya corriste una parte de la versión anterior, salta lo que ya exista.


-- 1. Sedes
CREATE TABLE sites (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  company_id  INT             NOT NULL,
  name        VARCHAR(120)    NOT NULL,
  created_at  BIGINT          NULL,
  updated_at  BIGINT          NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sites_company_name (company_id, name),
  CONSTRAINT fk_sites_company FOREIGN KEY (company_id) REFERENCES companies (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 2. Ubicación de los activos (NULL = sin sede / sin almacén)
ALTER TABLE units   ADD COLUMN site_id      BIGINT UNSIGNED NULL, ADD KEY idx_units_site (site_id);
ALTER TABLE tires   ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_tires_warehouse (warehouse_id);
ALTER TABLE sensors ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_sensors_warehouse (warehouse_id);
ALTER TABLE tboxes  ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_tboxes_warehouse (warehouse_id);


-- 3. Borrar lo de Quinta 1. Antes, confirmar que nada más apunta a esas tablas
--    (solo debe salir warehouse_tires -> warehouses):
SELECT TABLE_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IN ('warehouses', 'warehouse_tires');

DROP TABLE IF EXISTS warehouse_tires;
DROP TABLE IF EXISTS warehouses;
DROP TABLE IF EXISTS warehouses_bak_20261008;


-- 4. Almacenes nuevos: siempre dentro de una sede
CREATE TABLE warehouses (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  company_id  INT             NOT NULL,
  site_id     BIGINT UNSIGNED NOT NULL,
  name        VARCHAR(120)    NOT NULL,
  type        ENUM('general', 'scrap', 'retreading') NOT NULL DEFAULT 'general',
  created_at  BIGINT          NULL,
  updated_at  BIGINT          NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_warehouses_site_name (site_id, name),
  KEY idx_warehouses_company (company_id),
  CONSTRAINT fk_warehouses_company FOREIGN KEY (company_id) REFERENCES companies (id),
  CONSTRAINT fk_warehouses_site    FOREIGN KEY (site_id)    REFERENCES sites (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 5. Verificación: las 6 filas deben salir
SELECT TABLE_NAME, COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND ((TABLE_NAME = 'units' AND COLUMN_NAME = 'site_id')
    OR (TABLE_NAME IN ('tires', 'sensors', 'tboxes') AND COLUMN_NAME = 'warehouse_id')
    OR (TABLE_NAME = 'warehouses' AND COLUMN_NAME = 'site_id')
    OR (TABLE_NAME = 'sites' AND COLUMN_NAME = 'id'));


-- Rollback:
--   DROP TABLE warehouses;
--   ALTER TABLE tboxes  DROP INDEX idx_tboxes_warehouse,  DROP COLUMN warehouse_id;
--   ALTER TABLE sensors DROP INDEX idx_sensors_warehouse, DROP COLUMN warehouse_id;
--   ALTER TABLE tires   DROP INDEX idx_tires_warehouse,   DROP COLUMN warehouse_id;
--   ALTER TABLE units   DROP INDEX idx_units_site,        DROP COLUMN site_id;
--   DROP TABLE sites;
--   Los almacenes de Quinta 1 solo vuelven desde el snapshot.

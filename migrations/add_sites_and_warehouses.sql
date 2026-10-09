-- ============================================================================
--  Migración: Sedes y almacenes por compañía
--
--  Una SEDE (`sites`) es un lugar físico de la compañía (patio, base, planta).
--  Un ALMACÉN (`warehouses`) guarda inventario y SIEMPRE vive en una sede: no
--  existe almacén sin sede (site_id NOT NULL + FK, PASO 4). Las unidades
--  se asignan a una sede; llantas, sensores y Qbox a un almacén.
--
--  `warehouses` YA EXISTE: la trajo la migración de Quinta 1 (may-2025, 147
--  filas en 15 compañías) con la sede escrita como texto en `yard`. No se crea
--  otra tabla de almacenes: se le agrega `site_id` y se convierten esos textos
--  en sedes reales (PASO 3). `yard` se conserva y la API lo mantiene igual al
--  nombre de la sede, porque el índice UNIQUE (company_id, name, yard) es el que
--  impide dos almacenes con el mismo nombre en la misma sede.
--
--  La ubicación va como columna directa en cada activo (nunca tablas de
--  mapping, ver CLAUDE.md). `warehouse_tires` (2 filas de may-2025) no se usa.
--
--  Para los activos es ADITIVA: columnas NULL, lo existente queda sin ubicación
--  y todo sigue funcionando igual. En `warehouses` sí cambia algo: cada almacén
--  queda ligado a su sede y la columna pasa a obligatoria.
--
--  ⚠️ ORDEN: correr ANTES de mergear el PR. Al mergear a main se despliega dev,
--     y dev usa estas mismas tablas: el código nuevo escribe warehouse_id/site_id
--     al cambiar un activo de compañía y fallaría sin las columnas.
--
--  ⚠️ REQUISITOS: usuario master del RDS (app_user no tiene DDL), snapshot del
--     RDS y ventana de bajo tráfico. Cada ALTER hace auto-commit.
-- ============================================================================


-- ############################################################################
--  PASO 0 — BACKUP  (solo warehouses: es la única tabla cuyos DATOS se tocan)
--  >>> Si corres en otra fecha, cambia el sufijo _20261008 en TODO el archivo. <<<
-- ############################################################################

CREATE TABLE warehouses_bak_20261008 LIKE warehouses;
INSERT INTO warehouses_bak_20261008 SELECT * FROM warehouses;

SELECT (SELECT COUNT(*) FROM warehouses) AS orig, (SELECT COUNT(*) FROM warehouses_bak_20261008) AS bak;
-- >>> NO CONTINUAR si no coinciden. <<<


-- ############################################################################
--  PASO 1 — Tabla nueva `sites`
--  Sin is_deleted: una sede solo se borra vacía (sin unidades ni almacenes), y
--  así el UNIQUE (company_id, name) no choca con filas borradas.
-- ############################################################################

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


-- ############################################################################
--  PASO 2 — Columnas de ubicación
-- ############################################################################

ALTER TABLE warehouses ADD COLUMN site_id BIGINT UNSIGNED NULL,
                       ADD KEY idx_warehouses_site (site_id);

ALTER TABLE units   ADD COLUMN site_id      BIGINT UNSIGNED NULL, ADD KEY idx_units_site (site_id);
ALTER TABLE tires   ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_tires_warehouse (warehouse_id);
ALTER TABLE sensors ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_sensors_warehouse (warehouse_id);
ALTER TABLE tboxes  ADD COLUMN warehouse_id BIGINT UNSIGNED NULL, ADD KEY idx_tboxes_warehouse (warehouse_id);


-- ############################################################################
--  PASO 3 — Convertir el `yard` de Quinta 1 en sedes y ligar los almacenes
--  Hoy no hay almacenes con yard vacío (0 de 147). Si al correrla apareciera
--  alguno, se le pone la sede «Sede principal» de su compañía (no puede quedar
--  sin sede, ver PASO 4).
-- ############################################################################

UPDATE warehouses SET yard = 'Sede principal' WHERE TRIM(COALESCE(yard, '')) = '';

INSERT INTO sites (company_id, name, created_at, updated_at)
SELECT DISTINCT company_id, TRIM(yard), UNIX_TIMESTAMP() * 1000, UNIX_TIMESTAMP() * 1000
FROM warehouses;

UPDATE warehouses w
JOIN sites s ON s.company_id = w.company_id AND s.name = TRIM(w.yard)
SET w.site_id = s.id;


-- Todo almacén debe quedar con sede (sin_sede = 0):
SELECT COUNT(*) AS total, SUM(site_id IS NULL) AS sin_sede FROM warehouses;
-- >>> NO CONTINUAR si sin_sede > 0: el PASO 4 fallaría. <<<


-- ############################################################################
--  PASO 4 — Sede obligatoria: un almacén no existe sin sede
--  La FK además impide borrar una sede que todavía tenga almacenes.
-- ############################################################################

ALTER TABLE warehouses MODIFY site_id BIGINT UNSIGNED NOT NULL,
                       ADD CONSTRAINT fk_warehouses_site FOREIGN KEY (site_id) REFERENCES sites (id);


-- ############################################################################
--  PASO 5 — VERIFICACIÓN
-- ############################################################################

-- Sedes creadas por compañía (esperado hoy: 32 sedes en 15 compañías):
SELECT company_id, COUNT(*) AS sedes, GROUP_CONCAT(name ORDER BY name SEPARATOR ' | ') AS nombres
FROM sites GROUP BY company_id;

-- site_id debe salir obligatorio (IS_NULLABLE = NO):
SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'warehouses' AND COLUMN_NAME = 'site_id';

-- Los activos arrancan sin ubicación (las cuatro en 0):
SELECT 'units' AS tabla, COUNT(site_id) AS con_ubicacion FROM units
UNION ALL SELECT 'tires',   COUNT(warehouse_id) FROM tires
UNION ALL SELECT 'sensors', COUNT(warehouse_id) FROM sensors
UNION ALL SELECT 'tboxes',  COUNT(warehouse_id) FROM tboxes;


-- ############################################################################
--  ROLLBACK
-- ############################################################################
--
--    ALTER TABLE tboxes     DROP INDEX idx_tboxes_warehouse,  DROP COLUMN warehouse_id;
--    ALTER TABLE sensors    DROP INDEX idx_sensors_warehouse, DROP COLUMN warehouse_id;
--    ALTER TABLE tires      DROP INDEX idx_tires_warehouse,   DROP COLUMN warehouse_id;
--    ALTER TABLE units      DROP INDEX idx_units_site,        DROP COLUMN site_id;
--    ALTER TABLE warehouses DROP FOREIGN KEY fk_warehouses_site;
--    ALTER TABLE warehouses DROP INDEX idx_warehouses_site,   DROP COLUMN site_id;
--    UPDATE warehouses w JOIN warehouses_bak_20261008 b ON b.id = w.id SET w.yard = b.yard;
--    DROP TABLE sites;
--
--  El backup guarda los almacenes tal como estaban antes de que la API pueda
--  renombrarlos o cambiarles la sede; sirve para comparar o restaurar a mano.
--
--  LIMPIEZA, cuando esté estable:  DROP TABLE warehouses_bak_20261008;
-- ============================================================================

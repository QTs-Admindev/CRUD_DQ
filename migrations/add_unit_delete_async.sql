-- Borrado de unidad en segundo plano: marca de "borrando" y motivo si falla.
-- Correr con el usuario master ANTES de desplegar el código.


ALTER TABLE units
  ADD COLUMN deleting_at  BIGINT       NULL,
  ADD COLUMN delete_error VARCHAR(500) NULL;


-- Verificación: deben salir las 2 columnas.
SHOW COLUMNS FROM units WHERE Field IN ('deleting_at', 'delete_error');


-- Rollback:
--   ALTER TABLE units DROP COLUMN deleting_at, DROP COLUMN delete_error;

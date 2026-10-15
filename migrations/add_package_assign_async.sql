-- Asignación de paquete en segundo plano: estados 'assigning' y 'assign_failed' y
-- el avance que lee el FE. Correr con el usuario master ANTES de desplegar el código.


ALTER TABLE packages
  MODIFY COLUMN status ENUM('prepared','assigning','assigned','assign_failed','retired')
    NOT NULL DEFAULT 'prepared',
  ADD COLUMN assign_done  INT          NULL,
  ADD COLUMN assign_total INT          NULL,
  ADD COLUMN assign_error VARCHAR(500) NULL;


-- Verificación: el ENUM con los 5 estados y las 3 columnas nuevas.
SHOW COLUMNS FROM packages WHERE Field IN ('status', 'assign_done', 'assign_total', 'assign_error');


-- Rollback (solo si ningún paquete quedó en los estados nuevos):
--   SELECT status, COUNT(*) FROM packages GROUP BY status;
--   ALTER TABLE packages
--     MODIFY COLUMN status ENUM('prepared','assigned','retired') NOT NULL DEFAULT 'prepared',
--     DROP COLUMN assign_done, DROP COLUMN assign_total, DROP COLUMN assign_error;

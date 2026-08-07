-- Un Qbox pertenece a UNA sola unidad y un sensor a UNA sola llanta. Antes esto no tenía
-- respaldo en la BD, así que dos requests concurrentes podían dejar el mismo Qbox/sensor
-- reclamado por dos dueños. Estos índices lo hacen atómico.
--
-- Se usa una columna GENERADA que vale NULL para filas borradas (así una unidad/llanta
-- soft-deleted no bloquea reasignar su Qbox/sensor) y, en un UNIQUE, los NULL no colisionan
-- entre sí — permitiendo muchas filas sin Qbox/sensor pero solo un dueño vivo por dispositivo.
--
-- IMPORTANTE: si ya hay un Qbox/sensor con dos dueños vivos, el ALTER falla. Resolver antes:
--   SELECT tbox_id, COUNT(*) c FROM units
--     WHERE tbox_id IS NOT NULL AND (is_deleted IS NULL OR is_deleted=0)
--     GROUP BY tbox_id HAVING c > 1;
--   SELECT sensor_id, COUNT(*) c FROM tires
--     WHERE sensor_id IS NOT NULL AND (is_deleted IS NULL OR is_deleted=0)
--     GROUP BY sensor_id HAVING c > 1;

ALTER TABLE units
  ADD COLUMN tbox_owner_key INT
    GENERATED ALWAYS AS (CASE WHEN (is_deleted IS NULL OR is_deleted = 0) THEN tbox_id END) STORED,
  ADD UNIQUE INDEX uq_unit_tbox_owner (tbox_owner_key);

ALTER TABLE tires
  ADD COLUMN sensor_owner_key INT
    GENERATED ALWAYS AS (CASE WHEN (is_deleted IS NULL OR is_deleted = 0) THEN sensor_id END) STORED,
  ADD UNIQUE INDEX uq_tire_sensor_owner (sensor_owner_key);

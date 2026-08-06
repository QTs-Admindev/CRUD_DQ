-- Anti doble-montaje: impide que dos llantas VIVAS y montadas ocupen la misma
-- posición (unit_id, axle_index, wheel_index) en una unidad. Antes esto se validaba
-- solo en la app (check-then-act), que bajo concurrencia deja dos llantas en la misma
-- posición. Este índice lo hace atómico en la BD.
--
-- MySQL no soporta índices UNIQUE parciales (WHERE ...). Se emula con una columna
-- GENERADA que vale NULL cuando la llanta NO está montada (o está borrada) — y en un
-- índice UNIQUE los valores NULL NO colisionan entre sí — y "unit-axle-wheel" cuando sí
-- lo está. Así muchas llantas desmontadas conviven, pero solo una montada por posición.
--
-- IMPORTANTE: si ya existen posiciones duplicadas (por la carrera previa), este ALTER
-- FALLA. Primero hay que resolver los duplicados (ver la QA de conciliación). Query para
-- encontrarlos:
--   SELECT unit_id, axle_index, wheel_index, COUNT(*) c
--   FROM tires WHERE is_mounted = 1 AND (is_deleted IS NULL OR is_deleted = 0)
--   GROUP BY unit_id, axle_index, wheel_index HAVING c > 1;

ALTER TABLE tires
  ADD COLUMN mount_slot VARCHAR(48)
    GENERATED ALWAYS AS (
      CASE
        WHEN is_mounted = 1 AND (is_deleted IS NULL OR is_deleted = 0)
        THEN CONCAT_WS('-', unit_id, axle_index, wheel_index)
      END
    ) STORED,
  ADD UNIQUE INDEX uq_tire_mount_slot (mount_slot);

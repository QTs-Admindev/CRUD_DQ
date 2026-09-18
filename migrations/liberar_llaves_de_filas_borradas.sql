-- ============================================================================
--  Limpieza de una sola corrida: liberar las llaves que retienen las filas ya
--  borradas de antes.
--
--  CONTEXTO
--  El borrado es lógico (is_deleted = 1), pero los índices UNIQUE no saben de
--  eso: la fila borrada sigue ocupando su folio, su código o su identificador.
--  A partir de los PR #43 y #44 el borrado marca la llave al cerrarse, así que
--  esto NO se vuelve a acumular. Lo que este script arregla es el saldo anterior.
--
--  Hace exactamente lo mismo que hace ahora el código, con la misma marca:
--      '202'  ->  '202#del-4711'
--  El valor original se recupera cortando en '#del-' (llave_original en
--  shared/activation.py), así que la auditoría y los reportes no pierden nada.
--
--  PRE-CHECKS (2026-09-17, verificados en read-only sobre producción):
--    - Filas borradas con la llave aún sin marcar:
--         tires    36   sensors  21   tboxes  33   units  37        = 127
--    - Ninguna fila tiene ya la marca '#del-' (0 en las cuatro tablas), así que
--      no hay forma de encadenar marcas ni de chocar con una existente.
--    - Largo máximo actual de esas llaves: 15 / 12 / 12 / 19 caracteres.
--      Las columnas son varchar(255) en tires y units, y varchar(100) en sensors
--      y tboxes. Con la marca más larga posible ('#del-' + id) el peor caso queda
--      muy por debajo del límite: no hay truncamiento.
--    - No se toca NINGUNA fila viva: todos los WHERE llevan is_deleted = 1.
--
--  IDEMPOTENTE: el WHERE excluye lo que ya trae marca, así que correrlo dos
--  veces no encadena nada ni cambia nada la segunda vez.
--
--  REVERSIBLE: para deshacer, cortar en la marca —
--      UPDATE tires SET folio = SUBSTRING_INDEX(folio, '#del-', 1)
--      WHERE is_deleted = 1 AND folio LIKE '%#del-%';
--  (lo mismo en las otras tres). Deshacerlo devuelve el problema, no lo empeora.
--
--  ORDEN: correr DESPUÉS de mergear los PR #43 y #44. Antes no rompe nada, pero
--  el código viejo seguiría creando llaves retenidas y habría que repetirlo.
-- ============================================================================

-- ─── PASO 0 — ver qué se va a tocar, sin tocarlo ────────────────────────────
-- Correr esto primero y guardar el resultado. Es la foto de antes.

SELECT 'tires' AS tabla, COUNT(*) AS a_liberar FROM tires
  WHERE is_deleted = 1 AND folio IS NOT NULL AND folio <> ''
    AND folio NOT LIKE '%#del-%' AND folio NOT LIKE '%\\_\\_del%'
UNION ALL
SELECT 'sensors', COUNT(*) FROM sensors
  WHERE is_deleted = 1 AND sensorCode IS NOT NULL AND sensorCode <> ''
    AND sensorCode NOT LIKE '%#del-%' AND sensorCode NOT LIKE '%\\_\\_del%'
UNION ALL
SELECT 'tboxes', COUNT(*) FROM tboxes
  WHERE is_deleted = 1 AND tboxCode IS NOT NULL AND tboxCode <> ''
    AND tboxCode NOT LIKE '%#del-%' AND tboxCode NOT LIKE '%\\_\\_del%'
UNION ALL
SELECT 'units', COUNT(*) FROM units
  WHERE is_deleted = 1 AND unit_identifier IS NOT NULL AND unit_identifier <> ''
    AND unit_identifier NOT LIKE '%#del-%' AND unit_identifier NOT LIKE '%\\_\\_del%';

-- Y el detalle, por si alguien quiere revisar la lista antes:
SELECT id, folio, company_id, updated_at FROM tires
 WHERE is_deleted = 1 AND folio NOT LIKE '%#del-%' AND folio NOT LIKE '%\\_\\_del%'
 ORDER BY company_id, folio;


-- ─── PASO 1 — liberar ───────────────────────────────────────────────────────
-- Una transacción para las cuatro: o quedan todas o ninguna.

START TRANSACTION;

UPDATE tires
   SET folio = CONCAT(folio, '#del-', id)
 WHERE is_deleted = 1
   AND folio IS NOT NULL AND folio <> ''
   AND folio NOT LIKE '%#del-%' AND folio NOT LIKE '%\\_\\_del%';

UPDATE sensors
   SET sensorCode = CONCAT(sensorCode, '#del-', id)
 WHERE is_deleted = 1
   AND sensorCode IS NOT NULL AND sensorCode <> ''
   AND sensorCode NOT LIKE '%#del-%' AND sensorCode NOT LIKE '%\\_\\_del%';

UPDATE tboxes
   SET tboxCode = CONCAT(tboxCode, '#del-', id)
 WHERE is_deleted = 1
   AND tboxCode IS NOT NULL AND tboxCode <> ''
   AND tboxCode NOT LIKE '%#del-%' AND tboxCode NOT LIKE '%\\_\\_del%';

UPDATE units
   SET unit_identifier = CONCAT(unit_identifier, '#del-', id)
 WHERE is_deleted = 1
   AND unit_identifier IS NOT NULL AND unit_identifier <> ''
   AND unit_identifier NOT LIKE '%#del-%' AND unit_identifier NOT LIKE '%\\_\\_del%';

-- Antes del COMMIT: revisar que los conteos de abajo den lo esperado.
-- Si algo no cuadra -> ROLLBACK;

-- Ninguna llave se pasó de su columna (las cuatro deben dar 0):
SELECT 'tires>255'  AS revisa, COUNT(*) FROM tires   WHERE CHAR_LENGTH(folio) > 255
UNION ALL SELECT 'units>255',  COUNT(*) FROM units    WHERE CHAR_LENGTH(unit_identifier) > 255
UNION ALL SELECT 'sensors>100',COUNT(*) FROM sensors  WHERE CHAR_LENGTH(sensorCode) > 100
UNION ALL SELECT 'tboxes>100', COUNT(*) FROM tboxes   WHERE CHAR_LENGTH(tboxCode) > 100;

-- Ninguna fila VIVA fue tocada (las cuatro deben dar 0):
SELECT 'tires vivas marcadas'  AS revisa, COUNT(*) FROM tires   WHERE is_deleted = 0 AND folio LIKE '%#del-%'
UNION ALL SELECT 'sensors vivos marcados', COUNT(*) FROM sensors WHERE is_deleted = 0 AND sensorCode LIKE '%#del-%'
UNION ALL SELECT 'tboxes vivos marcados',  COUNT(*) FROM tboxes  WHERE is_deleted = 0 AND tboxCode LIKE '%#del-%'
UNION ALL SELECT 'units vivas marcadas',   COUNT(*) FROM units   WHERE is_deleted = 0 AND unit_identifier LIKE '%#del-%';

COMMIT;


-- ─── PASO 2 — comprobar que las llaves quedaron libres ──────────────────────
-- Debe bajar a 0: ya no hay llaves retenidas por filas muertas.

SELECT 'folios retenidos' AS que, COUNT(*) AS quedan FROM (
  SELECT prefix, folio, company_id FROM tires
   GROUP BY prefix, folio, company_id
  HAVING SUM(CASE WHEN is_deleted = 1 THEN 1 ELSE 0 END) = COUNT(*)
     AND MAX(folio) NOT LIKE '%#del-%'
) x;

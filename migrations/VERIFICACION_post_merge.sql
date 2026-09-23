-- ============================================================================
--  Qué mirar los primeros días después de mergear los PR #43 y #44.
--
--  Mergear a `dev` despliega solo, y `dev` apunta a las tablas REALES desde el
--  cutover del 6 de julio. No hay ventana de ensayo: cuando se acepta el PR, sale.
--
--  Estas consultas son de SOLO LECTURA y no dependen de logs ni de CloudWatch.
--  Correrlas al día siguiente y otra vez a la semana.
--
--  Lo que cada una responde está escrito arriba de ella, con el número que se vio
--  ANTES del merge (2026-09-17) para poder comparar.
-- ============================================================================

-- ─── 1. ¿El barrido está sanando o está desactivando de más? ────────────────
-- LA MÁS IMPORTANTE. El barrido D limpia el daijin_id de los activos que la
-- plataforma dice no tener, y los devuelve a 'registering' para que el barrido A
-- los registre otra vez. Si funciona, este número BAJA (el A los recoge). Si
-- SUBE día con día, el barrido está desactivando activos sanos y hay que apagarlo
-- poniendo VERIFY_MAX_ROWS=0 en el entorno de la Lambda (no requiere revert).
--
-- ANTES DEL MERGE (2026-09-17): units 0 · tires 0 · sensors 2 · tboxes 0.
-- El rezago ya está drenado, así que cualquier crecimiento sostenido se nota.

SELECT 'units'   AS tabla, COUNT(*) AS registering FROM units   WHERE is_deleted = 0 AND daijin_id IS NULL
UNION ALL SELECT 'tires',   COUNT(*) FROM tires   WHERE is_deleted = 0 AND daijin_id IS NULL
UNION ALL SELECT 'sensors', COUNT(*) FROM sensors WHERE is_deleted = 0 AND daijin_id IS NULL
UNION ALL SELECT 'tboxes',  COUNT(*) FROM tboxes  WHERE is_deleted = 0 AND daijin_id IS NULL;


-- ─── 2. El estado que ya no debería existir: activo sin id ──────────────────
-- Una fila 'active' sin daijin_id se sale de /resync y del cron: nadie la vuelve
-- a tocar. Los PR cierran los caminos que la producían.
-- ANTES DEL MERGE (2026-09-17): 0 en 'active'. Hay 2 sensores sin id, uno en
-- 'registering' y otro con el status en NULL — ese segundo no lo produce ningún
-- endpoint de hoy y vale la pena mirarlo, pero no bloquea nada.

SELECT 'sensors' AS tabla, status, COUNT(*) AS filas
  FROM sensors WHERE is_deleted = 0 AND daijin_id IS NULL GROUP BY status
UNION ALL
SELECT 'tboxes', status, COUNT(*)
  FROM tboxes  WHERE is_deleted = 0 AND daijin_id IS NULL GROUP BY status;


-- ─── 3. ¿Se está liberando la llave al borrar? ──────────────────────────────
-- Debe CRECER con el tiempo: cada borrado nuevo deja su llave marcada. Si se
-- queda en el número de la limpieza de una vez y no sube, el borrado no está
-- liberando y hay que mirar por qué.

SELECT 'tires'   AS tabla, COUNT(*) AS llaves_liberadas FROM tires   WHERE folio LIKE '%#del-%'
UNION ALL SELECT 'sensors', COUNT(*) FROM sensors WHERE sensorCode      LIKE '%#del-%'
UNION ALL SELECT 'tboxes',  COUNT(*) FROM tboxes  WHERE tboxCode        LIKE '%#del-%'
UNION ALL SELECT 'units',   COUNT(*) FROM units   WHERE unit_identifier LIKE '%#del-%';


-- ─── 4. Que no se repitan folios nuevos ─────────────────────────────────────
-- ANTES DEL MERGE: 32 grupos. Ese número puede BAJAR (si alguien refolia) pero
-- NO debe subir: el alta y el editar ya no dejan crear uno nuevo. Si sube, algo
-- está escribiendo folios sin pasar por los endpoints.

SELECT COUNT(*) AS folios_repetidos_vivos FROM (
  SELECT folio, company_id FROM tires WHERE is_deleted = 0
   GROUP BY folio, company_id HAVING COUNT(*) > 1
) x;


-- ─── 5. Que el editar esté dejando fecha ────────────────────────────────────
-- ANTES DEL MERGE: 375 llantas vivas sin updated_at. Ese número no baja solo
-- (las viejas se quedan como están), pero no debe SUBIR: toda edición nueva la
-- estampa. Si sube, alguna ruta de escritura se saltó el cambio.

SELECT COUNT(*) AS llantas_sin_fecha FROM tires
 WHERE is_deleted = 0 AND updated_at IS NULL;


-- ─── 6. Llantas que se contradicen ──────────────────────────────────────────
-- ANTES DEL MERGE (2026-09-17): 94 en total — 30 montadas sin unidad, 24
-- guardadas con unidad, 3 montadas sin posición, y el resto guardadas con la
-- posición sin limpiar. Este número NO lo arreglan los PR —falta validar la
-- coherencia de los cinco campos de montaje en el alta, que quedó fuera a
-- propósito. Se mide para saber si sigue creciendo.

SELECT
  SUM(CASE WHEN is_mounted = 1 AND unit_id IS NULL THEN 1 ELSE 0 END)     AS montada_sin_unidad,
  SUM(CASE WHEN is_mounted = 0 AND unit_id IS NOT NULL THEN 1 ELSE 0 END) AS guardada_con_unidad,
  SUM(CASE WHEN is_mounted = 1 AND (mount_position IS NULL OR mount_position = -1)
           THEN 1 ELSE 0 END)                                             AS montada_sin_posicion
  FROM tires WHERE is_deleted = 0;


-- ─── 7. El resumen del cron ─────────────────────────────────────────────────
-- Esto NO está en la base: sale en la respuesta de la Lambda `reconcile` y en
-- CloudWatch. Lo que hay que buscar es la llave `verify_aborted`.
--
--   verify_aborted > 0  ->  el cortacircuitos frenó: la plataforma contestó
--                           "no está" para demasiados activos a la vez. El
--                           barrido NO tocó nada, que es lo correcto, pero hay
--                           que ir a ver por qué contesta así (¿cambió el
--                           endpoint? ¿el token quedó con otro alcance?).
--
--   phantom_cleared     ->  activos que de verdad ya no estaban allá.
--   verify_skipped      ->  no se pudo preguntar; se reintenta solo.
--   verified            ->  revisados y correctos. Debería ser la mayoría.

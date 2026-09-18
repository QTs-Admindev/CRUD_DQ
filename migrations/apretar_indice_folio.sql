-- ============================================================================
--  Apretar el índice de tires para que la base sostenga la regla del folio.
--
--  NO CORRER TODAVÍA. Falta un paso de campo. Ver "BLOQUEADOR" abajo.
--
--  LA REGLA
--  El `id` es lo interno: amarra la llanta con la plataforma, con sus sensores y
--  con su historia. El prefijo y el folio son lo que ve el usuario para
--  identificarla, y por eso **el folio no se repite dentro de una compañía**,
--  aunque el prefijo sea distinto.
--
--  Hoy el índice es (prefix, folio, company_id), que NO dice eso: deja convivir
--  dos llantas vivas con el mismo folio si el prefijo difiere. La regla vive en
--  el código (alta y editar, PR #44). Mientras siga así, cualquier cosa que no
--  pase por esos dos endpoints —un script, SQL directo, una importación, un
--  endpoint nuevo— puede volver a meter un duplicado. Esta migración es lo que
--  convierte la costumbre en garantía.
--
--  BLOQUEADOR (medido 2026-09-17)
--  Hay 32 folios repetidos entre llantas VIVAS de la misma compañía: 66 llantas
--  en 3 compañías, 16 de ellas montadas y rodando. El ADD de abajo falla con
--  ER_DUP_ENTRY mientras existan.
--
--      QuintaTech (2)                 46 llantas,  5 montadas
--      Express y cargas culiacán (103) 18 llantas,  9 montadas
--      Logística 5 (8)                 2 llantas,  2 montadas
--
--  Refoliar una llanta montada es trabajo de campo: alguien tiene que ir al
--  camión, ver cuál es y que el folio nuevo concuerde con lo que trae grabado.
--  De los 32, solo UNO es crítico —el folio 82512 de Logística 5, con dos
--  llantas rodando a la vez en unidades distintas—; 14 tienen la gemela en
--  inventario y 17 tienen las dos guardadas.
--
--  NUNCA cambiar el `id` para resolverlo: es lo que amarra la llanta con la
--  plataforma y con su historia. Solo se toca el folio.
-- ============================================================================

-- ─── PASO 0 — la compuerta. Tiene que dar 0 para poder seguir ───────────────

SELECT COUNT(*) AS folios_repetidos_vivos FROM (
  SELECT folio, company_id
    FROM tires
   WHERE is_deleted = 0
   GROUP BY folio, company_id
  HAVING COUNT(*) > 1
) x;
-- 0  -> seguir al paso 1.
-- >0 -> PARAR. La lista con el detalle está en el documento
--       Quinta-CRUD-Como-Quedo.html, sección 08.


-- ─── PASO 1 — el índice nuevo ───────────────────────────────────────────────
-- Se crea el nuevo ANTES de tirar el viejo: si el ADD falla, nada cambió.
-- Requiere usuario con DDL (app_user NO lo tiene). Correr con el admin del RDS.
--
-- OJO con el borrado lógico: las filas con is_deleted = 1 también cuentan para
-- el UNIQUE. Por eso el índice solo es viable DESPUÉS de correr
-- liberar_llaves_de_filas_borradas.sql, que saca del índice a las muertas.

ALTER TABLE tires
  ADD CONSTRAINT uq_tires_folio_company UNIQUE (folio, company_id);


-- ─── PASO 2 — quitar el viejo, que ya sobra ─────────────────────────────────
-- El índice `prefix` (prefix, folio, company_id) queda cubierto por el nuevo
-- para lo que importa. Hacerlo en una sesión APARTE, un día después, cuando ya
-- se vio que nada se rompió: mientras los dos coexisten no estorban.

-- ALTER TABLE tires DROP INDEX prefix;


-- ─── VERIFICACIÓN ───────────────────────────────────────────────────────────

SHOW INDEX FROM tires WHERE Non_unique = 0;
-- Debe aparecer uq_tires_folio_company (folio, company_id).

-- Y que el alta siga pudiendo repetir folio ENTRE compañías distintas, que eso
-- sí está permitido y hoy pasa en 29 folios:
SELECT COUNT(*) AS folios_en_varias_companias FROM (
  SELECT folio FROM tires WHERE is_deleted = 0
   GROUP BY folio HAVING COUNT(DISTINCT company_id) > 1
) x;
-- Debe seguir dando 29. Si diera 0, el índice se puso sobre (folio) a secas y
-- está mal: hay que tirarlo.


-- ─── SI HAY QUE VOLVER ATRÁS ────────────────────────────────────────────────
-- ALTER TABLE tires DROP INDEX uq_tires_folio_company;
-- ALTER TABLE tires ADD UNIQUE INDEX prefix (prefix, folio, company_id);

# Cambios de BD para la migración a producción

> **Qué es esto:** el registro acumulado de **todos** los cambios de esquema que probamos
> en las tablas `test_`, listos para aplicar a las tablas **reales** cuando se haga la
> migración. Cada cambio incluye el SQL, el por qué, y las implicaciones en el código.
>
> **Importante:** requiere el usuario **MASTER/admin** del RDS (`app_user` no tiene DDL).
> Antes de aplicar a real: snapshot + seguir los gates de `PLAN_pruebas_riesgos_backup.md`.

---

## Resumen de cambios

| # | Cambio | Tablas | Estado en test_ | Estado en real |
|---|---|---|---|---|
| 1 | Columna `daijin_id` | units, tires, sensors, tboxes | ✅ aplicado | ⏳ pendiente |
| 2 | Backfill `daijin_id` desde `*_id_mapping` | idem | ✅ aplicado | ⏳ pendiente |
| 3 | Índice UNIQUE en `daijin_id` | idem | ✅ validado viable | ⏳ pendiente |
| 4 | Tabla `asset_audit_log` | (nueva) | — | ✅ ya creada en real |
| 5 | Columna `is_deleted` (soft delete) | units, tires, sensors, tboxes | ⏳ por aplicar | ⏳ pendiente |
| 6 | (Futuro) Retirar tablas `*_id_mapping` | mapping | — | ⏳ pendiente (al final) |
| 7 | Folio único por compañía (regla en código) | tires | ✅ en código | ⏳ constraint DB opcional |
| 8 | `company_id` nullable (inventario sin asignar) | sensors | ⏳ por aplicar | ⏳ pendiente |

---

## Cambio 1 — Columna `daijin_id`

Reemplaza las tablas `*_id_mapping` guardando el ID de Dajin como columna directa.

```sql
ALTER TABLE units   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE tires   ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE sensors ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
ALTER TABLE tboxes  ADD COLUMN daijin_id VARCHAR(50) NULL AFTER company_id;
```
- **Tipo:** `VARCHAR(50)` (Dajin lo maneja como string; ej. `"33521"`).
- **NULL** permitido: un activo en `registering` aún no tiene `daijin_id`.

## Cambio 2 — Backfill desde las tablas de mapping

```sql
UPDATE units u   JOIN (SELECT quinta_id, MAX(daijin_id) d FROM unit_id_mapping   GROUP BY quinta_id) m ON m.quinta_id=u.id  SET u.daijin_id=m.d WHERE u.daijin_id IS NULL;
UPDATE tires t   JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tire_id_mapping   GROUP BY quinta_id) m ON m.quinta_id=t.id  SET t.daijin_id=m.d WHERE t.daijin_id IS NULL;
UPDATE sensors s JOIN (SELECT quinta_id, MAX(daijin_id) d FROM sensor_id_mapping GROUP BY quinta_id) m ON m.quinta_id=s.id  SET s.daijin_id=m.d WHERE s.daijin_id IS NULL;
UPDATE tboxes b  JOIN (SELECT quinta_id, MAX(daijin_id) d FROM tbox_id_mapping   GROUP BY quinta_id) m ON m.quinta_id=b.id  SET b.daijin_id=m.d WHERE b.daijin_id IS NULL;
```
- Regla `MAX(daijin_id)` para activos con >1 mapping.
- **Validado en ensayo:** cobertura exacta, 0 desajustes, sin duplicados (UNIQUE viable).

## Cambio 3 — Índice UNIQUE en `daijin_id`

```sql
ALTER TABLE units   ADD UNIQUE KEY uq_units_daijin   (daijin_id);
ALTER TABLE tires   ADD UNIQUE KEY uq_tires_daijin   (daijin_id);
ALTER TABLE sensors ADD UNIQUE KEY uq_sensors_daijin (daijin_id);
ALTER TABLE tboxes  ADD UNIQUE KEY uq_tboxes_daijin  (daijin_id);
```
- MySQL permite múltiples NULL en UNIQUE → no afecta a los `registering`.
- Impide que dos activos locales reclamen el mismo `daijin_id`.

## Cambio 4 — Tabla `asset_audit_log`  (YA CREADA en real)

Log de auditoría append-only (quién/qué/cómo/resultado). Reemplaza a
`asset_creation_reports`/`asset_update_reports`. Pendiente: cablear `shared/audit.py`.

## Cambio 5 — Columna `is_deleted` (soft delete)

En vez de borrar filas, se marca `is_deleted=1`; en Dajin sí se borra de verdad.
Conserva el registro + `daijin_id` para historial/trazabilidad.

```sql
ALTER TABLE units   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tires   ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE sensors ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE tboxes  ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0;
```
- Precedente en el schema: `companies.is_deleted` ya existe; `tires.is_discarded` es similar.
- **Implicaciones en el código (pendientes de implementar):**
  - Las lecturas/listas deben filtrar `WHERE is_deleted = 0` (no mostrar borrados).
  - La idempotencia de creación debe considerar `is_deleted`.
  - Endpoint de borrado: `DELETE /...` → Dajin delete + `UPDATE ... SET is_deleted=1`.

## Cambio 6 — (Futuro) Retirar tablas `*_id_mapping`

Solo cuando `Quinta2.0BEGQL` deje de usarlas (grep `_id_mapping` = 0). Renombrar primero
(`_deprecated_*`), período de gracia, luego `DROP`.

```sql
-- RENAME TABLE unit_id_mapping TO _deprecated_unit_id_mapping;  -- etc.
```

## Cambio 7 — Folio de llanta único por compañía

Regla: el `folio` no se puede repetir dentro de la misma `company_id`, pero **sí** puede
repetirse entre compañías distintas. Aplicado en el código (`functions/tires/create.py`):
la llave de unicidad/idempotencia es `(folio, company_id)`; si llega un folio ya usado en
la compañía con otro `prefix`, responde **409**.

- **Opcional (defensa en BD):** cambiar el índice único de `tires`.
  La tabla real hoy tiene `UNIQUE (prefix, folio, company_id)`. Para forzar la regla a nivel BD:
  ```sql
  -- 1) verificar que NO existan folios duplicados por compañía (con distinto prefix):
  SELECT folio, company_id, COUNT(*) FROM tires GROUP BY folio, company_id HAVING COUNT(*) > 1;
  -- 2) si está limpio:
  ALTER TABLE tires DROP INDEX prefix;            -- el unique actual (prefix,folio,company_id)
  ALTER TABLE tires ADD UNIQUE KEY uq_tires_folio_company (folio, company_id);
  ```
  ⚠️ Solo si el paso 1 devuelve 0 filas. Si hay duplicados, resolverlos antes.

## Cambio 8 — Inventario de sensores/tboxes sin compañía (`company_id` nullable)

Modelo: los sensores y tboxes (hardware que llega físicamente) se **registran en
inventario SIN compañía** (`company_id = NULL`); la compañía se asigna después con una
operación independiente. La **empresa 2** (administradora, hardcodeada) ve todo el
inventario; cualquier otra ve solo lo suyo (`WHERE company_id = X`).

`sensors.company_id` hoy es `NOT NULL` (tboxes ya es nullable). Para permitir el NULL:

```sql
ALTER TABLE sensors MODIFY COLUMN company_id BIGINT NULL;
```

**Reglas de bloqueo (en código):** no se puede reasignar compañía ni borrar un sensor/tbox
si está vinculado (sensor en `tires.sensor_id`, tbox en `units.tbox_id`) → responde 409.

**Implicaciones en el código (pendientes):**
- Create de sensor/tbox: ya no recibe `company_id` (queda NULL). Se quita `version`.
- `/list/sensors|tboxes`: si la empresa es la 2 → sin filtro de company; si no → filtra.
- Nuevo endpoint "assign" para setear `company_id` (con guard de "no vinculado").
- Delete: guard de "no vinculado".

---

## Orden recomendado para aplicar a real
1. Snapshot del RDS.
2. Cambios 1, 5 (ADD COLUMN — aditivos, instantáneos, invisibles al sistema viejo).
3. Cambio 2 (backfill `daijin_id`).
4. Cambio 3 (UNIQUE) — tras confirmar 0 duplicados con la query de verificación.
5. Cutover del código (TABLE_PREFIX vacío) + cambios en BEGQL.
6. Cambio 6 (retiro de mapping) — al final, con red de seguridad.

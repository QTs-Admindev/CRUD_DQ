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

---

## Orden recomendado para aplicar a real
1. Snapshot del RDS.
2. Cambios 1, 5 (ADD COLUMN — aditivos, instantáneos, invisibles al sistema viejo).
3. Cambio 2 (backfill `daijin_id`).
4. Cambio 3 (UNIQUE) — tras confirmar 0 duplicados con la query de verificación.
5. Cutover del código (TABLE_PREFIX vacío) + cambios en BEGQL.
6. Cambio 6 (retiro de mapping) — al final, con red de seguridad.

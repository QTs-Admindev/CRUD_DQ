# Plan de Pruebas, Riesgos y Backup — Migración daijin_id

> Regla de oro: **nada se monta en prod sin estar probado contra los clones `test_`, con backup verificado y un rollback definido.** El sistema viejo NO se apaga hasta que el nuevo esté validado.

---

## 1. Principios de seguridad (no negociables)

1. **Prod-only ⇒ máxima cautela.** No hay dev; cada escritura a prod va con snapshot previo.
2. **Aditivo y reversible primero.** `ADD COLUMN` nullable + backfill idempotente; lo destructivo (drop) va al final, con rename y período de gracia.
3. **Coexistencia, no big-bang.** Viejo y nuevo conviven; el corte es gradual y reversible.
4. **El match es recuperable.** El `tyreCode` en Dajin = id local ⇒ ante desastre se puede reconstruir el `daijin_id` desde la API.
5. **Probar el código contra `test_` antes de cualquier deploy.**

---

## 2. FODA

### Fortalezas (internas, a favor)
- Migración **ya validada en ensayo** sobre datos reales: backfill exacto, 0 desajustes, `UNIQUE` viable sin limpiar.
- Datos chicos (~3k filas/tabla) ⇒ operaciones rápidas, snapshots baratos.
- Cambios aditivos y reversibles; backfill idempotente (re-ejecutable).
- Dependencias **acotadas y conocidas**: solo `Quinta2.0BEGQL` + asset-manager viejo tocan el mapping.
- Org-wide: nadie usa ORM con auto-sync, ningún INSERT posicional ⇒ coexistencia segura.
- `status='registering'` ya existe ⇒ red de seguridad nativa.

### Debilidades (internas, en contra)
- **Prod-only**, sin staging real.
- `app_user` sin permisos DDL ⇒ se depende del usuario master de RDS.
- Código nuevo (CRUD_DQ) aún **no adaptado** a la BD real ni probado.
- `log_bin_trust_function_creators=OFF` ⇒ triggers con fricción (se opta por dual-write).
- Documentación del sistema viejo pobre; conocimiento concentrado.

### Oportunidades (externas, a favor)
- Eliminar deuda técnica grande: Step Functions, 10 repos, 4 tablas de mapping.
- Simplificar a 7 tablas + Lambdas síncronas mantenibles.
- **Matar el bug de duplicados** de raíz (idempotencia GET-antes-de-POST).
- Ganar observabilidad: registering + reconciliación + log de "blame".

### Amenazas (externas, riesgo)
- **Prod vivo**: el sistema viejo sigue creando activos durante la transición ⇒ riesgo de desincronización.
- Caída/timeout de Dajin durante pruebas o cutover.
- Romper el **frontend** vía BEGQL si un cambio sale mal.
- **Error humano** en SQL ejecutado sobre tablas reales (no `test_`).

---

## 3. Registro de riesgos (probabilidad × impacto → mitigación + rollback)

| # | Riesgo | Prob | Impacto | Mitigación | Rollback |
|---|---|---|---|---|---|
| R1 | Error humano: SQL destructivo sobre tabla real en vez de `test_` | Media | **Crítico** | Scripts revisados; prefijo `test_`; `WHERE` siempre; snapshot antes de cada fase | Restore de snapshot |
| R2 | Desync: viejo crea activo y el nuevo no lo ve (o viceversa) | Alta | Medio | **Dual-write** durante transición + reconciliación diaria (`columna == mapping`) | Reconciliación rellena |
| R3 | Dajin caído/timeout en cutover | Media | Bajo | Local-first ⇒ queda en `registering`; barrido drena al volver | Ninguno (auto-recupera) |
| R4 | Colisión nueva de `daijin_id` creada entre ensayo y migración | Baja | Medio | **Re-validar D4** justo antes de aplicar `UNIQUE` | `DROP INDEX` |
| R5 | Backfill corre tarde; prod creó activos nuevos | Alta | Bajo | Backfill **idempotente**, re-ejecutar antes del cutover | Re-ejecutar |
| R6 | Código nuevo escribe mal el `daijin_id` | Media | Alto | Pruebas exhaustivas contra `test_`; feature flag | No desplegar / flag off |
| R7 | Drop de mapping rompe un consumidor no detectado | Baja | Alto | grep ya hecho; **rename antes de drop** + gracia de semanas | `RENAME` de vuelta |
| R8 | Cambio en BEGQL rompe el frontend | Media | Alto | Cambios quirúrgicos (2 funciones); probar contra `test_` | Revert del deploy |

---

## 4. Estrategia de Backup

1. **Snapshot RDS manual** antes de CADA fase que escriba en prod (no antes de lecturas). Nombrar con fecha/fase.
2. **Dump lógico** adicional de las 8 tablas core con `--single-transaction --no-lock` (no lockea prod) como respaldo portable.
3. **No dropear las mapping**: renombrarlas (`_deprecated_*`) y conservarlas semanas — son la fuente del `daijin_id`.
4. Verificar **snapshots automáticos** de RDS activos y su retención.
5. **Restore drill**: probar el restore en una instancia desechable AL MENOS una vez, para confirmar que el backup sirve (un backup no probado no es un backup).

---

## 5. Plan de pruebas del código (contra copia, antes de montar)

**Objetivo:** validar CRUD_DQ contra una copia aislada antes de tocar prod o desplegar.

- **Entorno:** apuntar CRUD_DQ a un schema de pruebas con los **nombres reales** de tabla (recomendado crear `quinta_test` como copia, mejor que usar prefijo `test_` que obliga a remapear nombres en el código).
- **Cobertura mínima:**
  - Create de los 4 activos (unit/tire/sensor/tbox) → escribe `daijin_id` + `status` correcto.
  - **Idempotencia**: GET-antes-de-POST no duplica al reintentar.
  - **Fallo de Dajin** (mock): queda en `registering`, no rompe, no escribe basura.
  - **Reconciliación**: barrido completa los `registering` pendientes.
  - **Invariante**: nunca queda `active` con `daijin_id NULL`.
  - Binds/unbinds (relación vs identidad).
- **Tipo:** unit tests con SmartTyre mockeado + integración contra `quinta_test`.
- **Gate:** no se despliega ni se migra hasta que toda esta suite pase.

---

## 6. Recuperación ante desastre (worst case)

**Escenario peor:** algo corrompe datos o el cutover falla a medias.

1. **El viejo sigue vivo** (no lo apagamos) ⇒ el sistema sigue operando aunque el nuevo falle.
2. **Restore del snapshot** previo ⇒ se vuelve al estado exacto anterior a la fase.
3. **Las mapping siguen existiendo** (solo renombradas) ⇒ el `daijin_id` nunca se pierde.
4. **Reconstrucción extrema**: si se perdiera el `daijin_id`, se relista en Dajin por `tyreCode` (= id local) y se rellena.
5. **Cutover con feature flag**: poder volver al viejo sin redeploy.

---

## 7. Secuencia con gates (orden de ejecución)

```
GATE 0  Pruebas de código contra quinta_test          → TODA la suite verde
GATE 1  FODA + riesgos revisados y aceptados           → este documento OK
GATE 2  Backup: snapshot + dump + restore drill        → restore probado
GATE 3  Migración real aditiva (ADD COLUMN + backfill)  → re-validar D1-D4
GATE 4  Dual-write activo + reconciliación corriendo    → 0 desajustes N días
GATE 5  Cutover gradual por tipo (tbox→sensor→tire→unit)
GATE 6  grep _id_mapping = 0 → rename mapping → (semanas) drop
```

> Ningún gate avanza si el anterior no pasó. Cada gate con escritura a prod ⇒ snapshot previo.

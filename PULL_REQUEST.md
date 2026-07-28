# hardening: autenticacion Cognito, alcance por empresa y actor verificable

Rama: `hardening/cruddq-auth`
Base: `main` (creada desde `e8cb75b`)

> ## ROMPE A LOS CLIENTES
>
> **Esta rama no se puede integrar sin coordinar con el frontend.** Hoy la API es
> completamente publica; despues de esta rama toda peticion sin un JWT valido de
> Cognito recibe `401`. Ningun cliente actual manda ese token.
>
> Antes de fusionar hacen falta dos decisiones marcadas con
> `TODO(before merge)`:
>
> 1. el ARN del user pool de Cognito (`COGNITO_USER_POOL_ARN`);
> 2. que los usuarios del pool tengan el atributo `custom:company_id` poblado y
>    que el personal de Quinta este en el grupo `quinta-admin`.
>
> Sin el punto 2 los usuarios quedan autenticados pero sin alcance, y reciben
> `403`. En particular, quien hoy dependa de `?company_id=2` para ver el
> inventario completo debe pasar a estar en el grupo admin.

## Resumen

Las 31 rutas se despliegan sin autorizador: cualquiera con la URL puede leer y
escribir el inventario completo de la flota, crear y borrar vehiculos, llantas,
sensores y Qboxes. Ademas, dos decisiones de seguridad se toman con datos que
escribe el propio cliente: la empresa cuyos activos se listan y el actor que
queda registrado en la bitacora de auditoria.

## Cambios

### 1. Autorizador Cognito en `serverless.yml`

Un recurso `AWS::ApiGateway::Authorizer` de tipo `COGNITO_USER_POOLS` que valida
el header `Authorization`, aplicado a **los 31 eventos HTTP**. La descripcion de
la tarea hablaba de 26 endpoints; el repositorio tiene ya 31 (crecio con los
paquetes y las asignaciones), todos cubiertos.

Se declara una vez con un ancla YAML (`&cognito`) en lugar de repetirse 31
veces. Las dos funciones sin ruta HTTP (`sensorsBulkSyncWorker`, invocada por la
carga masiva, y `reconcile`, que corre por cron) siguen sin exponerse, y hay una
prueba que lo fija.

### 2. El `company_id` sale del token y desaparece el bypass del numero 2

`functions/lists/list_assets.py`, nuevo `shared/auth.py`

`GET /list/{resource}` tomaba el `company_id` del query string, y este bloque:

```python
if company_id != ADMIN_COMPANY_ID:      # ADMIN_COMPANY_ID = 2
    filters["company_id"] = company_id
```

significaba que **`?company_id=2` no filtraba nada**. Cualquiera que conociera
ese numero, sin autenticarse, obtenia el inventario completo de toda la flota:
unidades, llantas, sensores, Qboxes y la bitacora de auditoria. Y con cualquier
otro numero obtenia el inventario de esa empresa.

El nuevo `shared/auth.py` deriva el alcance de las claims verificadas:

- **usuario normal**: anclado al `custom:company_id` de su token. El parametro de
  consulta solo puede coincidir con esa empresa; pedir otra es `403`.
- **staff de Quinta** (grupo `quinta-admin`): vista global, incluido el
  inventario sin asignar, y el parametro le sigue sirviendo como filtro.

El privilegio pasa a depender de la pertenencia a un grupo, no de un numero que
cualquiera puede teclear. Para un usuario de la empresa 2, el 2 se aplica ahora
como un filtro normal.

Los catalogos (`unit_catalog`, `tires_catalog`, `companies`) siguen siendo datos
de referencia compartidos, pero exigen identidad autenticada.

### 3. El actor de la bitacora se deriva de la identidad autenticada

`shared/audit.py`

El actor venia de la cabecera `X-Actor`, que escribe el propio cliente. Con eso,
cualquiera podia atribuir cualquier accion a cualquier persona, lo que deja la
bitacora sin valor probatorio justo cuando mas se necesita. Ahora sale de las
claims (`email`, con respaldo en `cognito:username` y `sub`).

`shared/audit.actor_from` se conserva como nombre publico y delega en
`shared/auth.actor_from`, asi que `functions/sensors/bulk_create.py` no cambia.
El respaldo `"system"` se mantiene solo para las invocaciones que no son HTTP: el
cron de reconciliacion y el worker de la carga masiva, que no llevan
`requestContext`.

## Lo que esta rama NO cierra

- **CORS sigue abierto.** Los 31 eventos usan `cors: true`, que emite
  `Access-Control-Allow-Origin: *`. Cerrarlo exige la lista de dominios reales
  del frontend, que no esta registrada en el repositorio. Con el autorizador
  puesto el riesgo baja mucho, pero conviene atarlo aparte.
- **Las escrituras no verifican la empresa del recurso.** El autorizador impide
  que un anonimo escriba, pero un usuario autenticado de la empresa A todavia
  puede pedir `DELETE /tires/{id}` sobre una llanta de la empresa B: los
  handlers de escritura no comparan `company_id`. Cerrarlo son ~20 handlers y
  merece su propia rama; esta ya es suficientemente grande.

## Pruebas

`python -m pytest tests/ -q` → **201 pruebas en verde** (partiendo de 149).

Este repositorio no tenia pruebas `test_KNOWN_GAP_*`, asi que no hubo ninguna
que convertir.

- `tests/conftest.py` gana helpers (`as_company`, `as_admin`, `auth_context`,
  `authed`) que construyen el evento de Lambda tal como lo entrega API Gateway.
  No hay dobles de `shared/auth`: se prueba la extraccion real de las claims,
  incluidos los seis casos de evento sin autorizador que deben dar `401`.
- `tests/test_list_assets.py` se reescribe alrededor del alcance: usuario normal
  anclado, peticion de otra empresa rechazada, y en particular dos pruebas
  dedicadas al bypass del numero 2 (que un cliente ya no lo pueda usar, y que
  para el usuario de la empresa 2 sea un filtro normal).
- `tests/test_autenticacion.py` cubre `shared/auth.py`, el actor de la bitacora
  (incluida una prueba donde la cabecera `X-Actor` miente y gana el token) y el
  cableado de `serverless.yml`, de forma que anadir un endpoint sin autorizador
  rompe la suite.
- `tests/test_bulk_create.py` deja de mandar el actor por cabecera y lo manda en
  las claims.

## Dependencias

- **Bloqueante y externa**: el user pool de Cognito, el atributo
  `custom:company_id` y el grupo `quinta-admin`. Sin eso el despliegue deja la
  API inaccesible.
- **Coordinacion con el frontend**: tiene que enviar
  `Authorization: Bearer <id_token>`, dejar de mandar `X-Actor` (se ignora) y
  dejar de usar `?company_id=2` como forma de ver todo.
- Sale del mismo HEAD que `hardening/cruddq-fixes`. No coinciden en ningun
  archivo salvo `PULL_REQUEST.md`, asi que se pueden integrar en cualquier orden;
  se recomienda la de arreglos primero por ser la de menor riesgo.
- `requirements-dev.txt` suma `pyyaml`, usado solo por las pruebas que
  inspeccionan `serverless.yml`.

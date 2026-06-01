\# CLAUDE.md — Asset Manager



Contexto completo del proyecto para asistentes AI. Léelo antes de tocar cualquier archivo.



\---



\## Qué es este proyecto



Sistema de gestión de activos de flota (vehículos, llantas, sensores TPMS, TBoxes GPS)

que sincroniza con \*\*SmartTyre API\*\* (plataforma del fabricante chino del hardware).



Toda operación de creación o modificación de un activo debe reflejarse en \*\*dos lugares\*\*:

1\. MySQL (base de datos propia)

2\. SmartTyre API (plataforma externa)



Si SmartTyre falla → no se escribe en MySQL.

Si MySQL falla después de SmartTyre → loguear el `smarttyre\_id` para rollback manual.



\---



\## Stack



| Elemento | Valor |

|---|---|

| Lenguaje | Python 3.12 |

| Runtime | AWS Lambda |

| Deploy | Serverless Framework v3 |

| Validación | Pydantic v2 |

| HTTP client | httpx (síncrono) |

| DB | MySQL vía PyMySQL |

| Credenciales | AWS Secrets Manager |

| Región AWS | us-west-1 |



\---



\## Estructura del Monorepo



```

asset-manager/

├── serverless.yml              # Config global de deploy

├── requirements.txt

├── shared/                     # Lambda Layer — código compartido entre todas las funciones

│   ├── smarttyre/

│   │   ├── client.py           # SmartTyreClient (HTTP + auth + firma)

│   │   ├── sign.py             # Firma MD5 requerida por SmartTyre

│   │   └── models.py           # Pydantic models de SmartTyre responses

│   ├── db/

│   │   ├── connection.py       # Singleton PyMySQL por container

│   │   └── ops.py              # insert / update / get\_by\_id / get\_by\_field

│   ├── secrets/

│   │   └── manager.py          # get\_secret() con cache en memoria

│   └── utils/

│       ├── response.py         # ok() / error() → dict Lambda response

│       └── validators.py       # validate\_hex12() para tboxCode/sensorCode

└── functions/                  # Una carpeta por Lambda

&#x20;   ├── vehicles/create.py

&#x20;   ├── vehicles/update.py

&#x20;   ├── vehicles/get.py

&#x20;   ├── tires/create.py

&#x20;   ├── tires/update.py

&#x20;   ├── sensors/create.py

&#x20;   ├── tboxes/create.py

&#x20;   ├── tboxes/update.py

&#x20;   └── bindings/

&#x20;       ├── bind\_tire.py

&#x20;       ├── unbind\_tire.py

&#x20;       ├── bind\_sensor.py

&#x20;       └── unbind\_sensor.py

```



\---



\## Decisión Arquitectónica Central: Sin Tablas de Mapping



El sistema anterior usaba tablas `truck\_id\_mapping (quinta\_id, daijin\_id)` para

traducir entre el ID local de MySQL y el ID de SmartTyre. \*\*Esto está eliminado.\*\*



\*\*Regla:\*\* El `smarttyre\_id` se guarda como columna directa en la misma tabla del activo.



```sql

\-- MAL (sistema anterior):

SELECT daijin\_id FROM truck\_id\_mapping WHERE quinta\_id = ?

\-- BIEN (este sistema):

SELECT smarttyre\_id FROM vehicles WHERE id = ?

```



\*\*Nunca crear tablas `\*\_id\_mapping`.\*\*



\---



\## Schema MySQL



```sql

vehicles (

&#x20; id INT PK AUTO\_INCREMENT,

&#x20; license\_plate VARCHAR(50) UNIQUE,  -- placa real, no autoincrement

&#x20; smarttyre\_id  VARCHAR(50),         -- ID asignado por SmartTyre

&#x20; company\_id INT, unit\_catalog\_id INT, unit\_identifier VARCHAR(100),

&#x20; is\_tractor TINYINT(1), tbox\_id INT, status ENUM('registering','active','inactive')

)



tires (

&#x20; id INT PK AUTO\_INCREMENT,

&#x20; tyre\_code    VARCHAR(50) UNIQUE,   -- natural key del fabricante

&#x20; smarttyre\_id VARCHAR(50),

&#x20; company\_id INT, vehicle\_id INT,

&#x20; prefix VARCHAR(50), folio VARCHAR(50),

&#x20; is\_mounted TINYINT(1), axle\_index INT, wheel\_index INT, mount\_position INT

)



sensors (

&#x20; id INT PK AUTO\_INCREMENT,

&#x20; sensor\_code  VARCHAR(50) UNIQUE,   -- 12 hex chars

&#x20; smarttyre\_id VARCHAR(50),

&#x20; company\_id INT, tire\_id INT

)



tboxes (

&#x20; id INT PK AUTO\_INCREMENT,

&#x20; tbox\_code    VARCHAR(50) UNIQUE,   -- 12 hex chars

&#x20; smarttyre\_id VARCHAR(50),

&#x20; company\_id INT, vehicle\_id INT,

&#x20; status ENUM('registering','active','inactive')

)

```



\---



\## SmartTyre API



\### Autenticación

```

POST /smartyre/openapi/auth/oauth20/authorize

Body: { clientId, clientSecret, grantType: "client\_credentials" }

→ Devuelve: { accessToken }

```



El token dura \~1 hora. Se cachea en variable global de módulo (persiste entre

warm invocations del mismo container Lambda). \*\*No pedir token nuevo en cada request.\*\*



\### Firma por request

Cada request lleva header `sign` calculado así:

```

raw = sorted(headers) → "key=val\&" para cada uno

&#x20;   + body + "\&"       (si hay body)

&#x20;   + sorted(params)   (si hay params)

&#x20;   + SIGN\_KEY

sign = MD5(raw.encode("utf-8")).hexdigest()

```



\### Headers obligatorios en todo request

```

clientId, timestamp (Unix ms), nonce (16 bytes hex), accessToken, sign

```



\### Problema: SmartTyre no devuelve el ID al crear



Al hacer `POST /tyre/insert` la respuesta es `"Success"`, no el ID generado.

Para obtener el `smarttyre\_id` hay que hacer un GET inmediatamente después,

filtrando por el natural key (`tyre\_code`, `tbox\_code`, `sensor\_code`):



```python

\# Patrón obligatorio al crear cualquier activo:

st.post("/smartyre/openapi/tyre/insert", tire\_data)  # → "Success"

resp = st.get("/smartyre/openapi/tyre/list", {"tyreCode": tyre\_code})

smarttyre\_id = resp\["records"]\[0]\["id"]  # ← así se obtiene

```



\### Endpoints principales



| Operación | Endpoint | Método |

|---|---|---|

| Auth | `/smartyre/openapi/auth/oauth20/authorize` | POST |

| Crear vehículo | `/smartyre/openapi/vehicle/insert` | POST |

| Actualizar vehículo | `/smartyre/openapi/vehicle/update` | POST |

| Listar vehículos | `/smartyre/openapi/vehicle/list` | GET |

| Crear llanta | `/smartyre/openapi/tyre/insert` | POST |

| Listar llantas | `/smartyre/openapi/tyre/list` | GET |

| Crear sensor | `/smartyre/openapi/sensor/insert` | POST |

| Listar sensores | `/smartyre/openapi/sensor/list` | GET |

| Crear TBox | `/smartyre/openapi/tbox/insert` | POST |

| Listar TBoxes | `/smartyre/openapi/tbox/list` | GET |

| Bind llanta→vehículo | `/smartyre/openapi/vehicle/tyre/bind` | POST |

| Unbind llanta | `/smartyre/openapi/vehicle/tyre/unbind` | POST |

| Bind sensor→llanta | `/smartyre/openapi/tyre/sensor/bind` | POST |

| Unbind sensor | `/smartyre/openapi/tyre/sensor/unbind` | POST |



\---



\## Secrets en AWS Secrets Manager



| Nombre | Tipo | Contenido |

|---|---|---|

| `SMARTTYRE\_BASE\_URL` | String | URL base de la API |

| `SMARTTYRE\_CLIENT\_ID` | String | clientId para auth |

| `SMARTTYRE\_CLIENT\_SECRET` | String | clientSecret para auth |

| `SMARTTYRE\_SIGN\_KEY` | String | clave para firma MD5 |

| `MYSQL\_DB\_URI` | JSON String | `{"host":"...","user":"...","password":"...","db":"..."}` |



\*\*Nunca hardcodear credenciales. Siempre usar `get\_secret()`.\*\*



\---



\## Patrón de Cada Lambda



Todo handler sigue exactamente esta estructura:



```python

import json

from pydantic import BaseModel, ValidationError

from shared.smarttyre.client import SmartTyreClient

from shared.db.connection import get\_db

from shared.db.ops import insert, get\_by\_id

from shared.utils.response import ok, error



class RequestModel(BaseModel):

&#x20;   campo: str

&#x20;   campo2: int



def handler(event, context):

&#x20;   # 1. Parsear y validar input

&#x20;   try:

&#x20;       body = RequestModel.model\_validate(json.loads(event.get("body") or "{}"))

&#x20;   except ValidationError as e:

&#x20;       return error(422, e.errors())



&#x20;   # 2. SmartTyre primero

&#x20;   try:

&#x20;       st = SmartTyreClient()

&#x20;       st.post("/smartyre/openapi/XXX/insert", body.model\_dump())

&#x20;   except Exception as e:

&#x20;       return error(502, f"SmartTyre error: {e}")



&#x20;   # 3. Obtener smarttyre\_id via GET (SmartTyre no lo devuelve en el create)

&#x20;   try:

&#x20;       resp = st.get("/smartyre/openapi/XXX/list", {"field": body.campo})

&#x20;       smarttyre\_id = resp\["records"]\[0]\["id"]

&#x20;   except Exception as e:

&#x20;       return error(502, f"SmartTyre ID lookup failed: {e}")



&#x20;   # 4. MySQL

&#x20;   try:

&#x20;       db = get\_db()

&#x20;       record = insert(db, "tabla", {

&#x20;           "campo": body.campo,

&#x20;           "smarttyre\_id": smarttyre\_id,

&#x20;       })

&#x20;       db.commit()

&#x20;       return ok(record)

&#x20;   except Exception as e:

&#x20;       db.rollback()

&#x20;       return error(500, f"DB error (SmartTyre ID={smarttyre\_id}): {e}")

```



\### Reglas del patrón

\- \*\*SmartTyre siempre va antes que MySQL\*\*

\- Si SmartTyre falla → retornar 502, no tocar DB

\- Si MySQL falla → loguear el `smarttyre\_id` en el mensaje de error (para rollback manual)

\- \*\*Nunca\*\* usar `try/except` genérico que trague errores silenciosamente

\- El `db.commit()` solo después de que todas las operaciones DB estén completas



\---



\## Validaciones Obligatorias



| Campo | Validación |

|---|---|

| `tbox\_code` | 12 caracteres, solo 0-9 y A-F (hexadecimal) |

| `sensor\_code` | 12 caracteres, solo 0-9 y A-F (hexadecimal) |

| `company\_id` | entero positivo |

| `license\_plate` | string no vacío |



```python

\# shared/utils/validators.py

import re

HEX12 = re.compile(r'^\[0-9A-Fa-f]{12}$')



def validate\_hex12(value: str, field\_name: str) -> str:

&#x20;   if not HEX12.match(value):

&#x20;       raise ValueError(f"{field\_name} debe ser 12 caracteres hexadecimales (0-9, A-F)")

&#x20;   return value.upper()

```



\---



\## Singleton DB — Cómo Funciona



`get\_db()` en `shared/db/connection.py` usa una variable global `\_conn`.

En Lambda, el módulo se inicializa una vez por container. Las warm invocations

reutilizan la misma conexión MySQL (no abrir/cerrar en cada request).



Si la conexión está cerrada (timeout), se reconecta automáticamente.



```python

\_conn = None



def get\_db():

&#x20;   global \_conn

&#x20;   if \_conn is None or not \_conn.open:

&#x20;       # reconectar

&#x20;       ...

&#x20;   return \_conn

```



\---



\## APIs Disponibles — Resumen



| Lambda | Método | Path |

|---|---|---|

| createVehicle | POST | `/vehicles` |

| updateVehicle | PUT | `/vehicles/{id}` |

| getVehicle | GET | `/vehicles/{id}` |

| createTire | POST | `/tires` |

| updateTire | PUT | `/tires/{id}` |

| createSensor | POST | `/sensors` |

| createTbox | POST | `/tboxes` |

| updateTbox | PUT | `/tboxes/{id}` |

| bindTireToVehicle | POST | `/vehicles/{id}/tires/bind` |

| unbindTireFromVehicle | POST | `/vehicles/{id}/tires/unbind` |

| bindSensorToTire | POST | `/tires/{id}/sensors/bind` |

| unbindSensorFromTire | POST | `/tires/{id}/sensors/unbind` |



\---



\## Comandos



```bash

\# Deploy a dev

npx serverless deploy --stage dev



\# Deploy a prod

npx serverless deploy --stage prod



\# Deploy una sola función

npx serverless deploy function -f createVehicle --stage dev



\# Ver logs en vivo

npx serverless logs -f createVehicle --stage dev --tail



\# Invocar localmente

npx serverless invoke local -f createVehicle --data '{"body":"{\\"license\_plate\\":\\"ABC-123\\"}"}'



\# Tests

pytest tests/ -v

```



\---



\## Lo que NO Hacer



\- ❌ No crear tablas `\*\_id\_mapping` — la arquitectura las eliminó explícitamente

\- ❌ No hardcodear credenciales ni URLs — siempre `get\_secret()`

\- ❌ No hacer `db.commit()` antes de que todas las operaciones DB estén completas

\- ❌ No escribir en MySQL si SmartTyre falló

\- ❌ No pedir un token nuevo a SmartTyre en cada invocación — usar el cache global

\- ❌ No usar `requests` — usar `httpx`

\- ❌ No poner lógica de negocio en `serverless.yml`




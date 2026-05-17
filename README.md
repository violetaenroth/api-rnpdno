# RNPDNO Downloader

Cliente async en Python para descargar datos de la API del [Registro Nacional de Personas Desaparecidas y No Localizadas](https://consultapublicarnpdno.segob.gob.mx/consulta) (RNPDNO).

Para más contexto sobre el proyecto y los datos, consultar la [página de GitHub](https://violetaenroth.github.io/api-rnpdno/).

## Instalación
Instalar el gestor de dependencias uv, y posteriormente ejecutar:

```bash
uv sync
```

## Uso rápido
```python
import asyncio
from rnpd_downloader import RNPDDownloader

async def main():
    async with RNPDDownloader() as dl:
        total = await dl.obtener_total()
        await dl.descargar(endpoint="matriz", storage="json")

asyncio.run(main())
```

En notebooks de Jupyter se puede usar `await` directamente ya que el event loop ya está corriendo:

```python
from rnpd_downloader import RNPDDownloader

dl = RNPDDownloader()
total = await dl.obtener_total()
await dl.descargar(endpoint="matriz", storage="json", filepath="datos.json")
await dl.cerrar()
```

## Endpoints disponibles

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| token | Automático | JWT de autenticación (se renueva solo) |
| get_paginador | `obtener_total()` | Conteo de registros con filtros opcionales |
| get_info_matriz | `descargar(endpoint="matriz")` | Datos de la vista cuadrícula |
| get_info_lista | `descargar(endpoint="lista")` | Datos de la vista lista (no sirve a mayo 2026) |
| municipios | `obtener_municipios("14")` | Catálogo de municipios por estado |

El token se renueva automáticamente cada 100 peticiones o cada 50 minutos (lo que ocurra primero). Estos valores son configurables, pero siempre hay que tomar en cuenta que la duración máxima del token es 60 minutos:

```python
dl = RNPDDownloader(requests_por_token=200, token_duracion=1800)
```


## Filtros

Todos los endpoints de datos (`obtener_total`, `descargar`) aceptan filtros opcionales. Si no se pasan filtros, se trabaja con el total de registros.

### Formato de cada filtro

| Filtro | Tipo | Formato | Ejemplo |
|--------|------|---------|---------|
| `estado` | str | ID numérico del estado | 14 (Jalisco) |
| `municipio` | str | ID numérico del municipio | 12 |
| `rango` | str | ? | "" |
| `fecha_inicio` | str | ISO 8601 con timezone UTC | 2024-01-01T00:00:00.000Z |
| `fecha_fin` | str | ISO 8601 con timezone UTC | 2024-12-31T23:59:59.999Z |

Los IDs de estado y municipio corresponden a los del catálogo de la API. Se pueden consultar con `obtener_municipios()`:

```python
# IDs de estado: "1"=Aguascalientes, "2"=Baja California, ..., "32"=Zacatecas
municipios = await dl.obtener_municipios("14")
```

Las fechas deben ir en formato ISO 8601 con milisegundos y sufijo `Z` para UTC. Ambas fechas (fecha_inicio y fecha_fin) deben proporcionarse juntas; si solo se pasa una, se ignoran las dos.

### Ejemplos

```python
#  total sin filtros
total = await dl.obtener_total()

# total filtrado por estado
total = await dl.obtener_total(estado="14")

# total filtrado por rango de fechas
total = await dl.obtener_total(
    fecha_inicio="2024-01-01T00:00:00.000Z",
    fecha_fin="2024-12-31T23:59:59.999Z",
)

# descarga con todos los filtros
await dl.descargar(
    endpoint="lista",
    storage="json",
    filepath="jalisco_2024.json",
    estado="14",
    municipio="12",
    fecha_inicio="2024-01-01T00:00:00.000Z",
    fecha_fin="2024-12-31T23:59:59.999Z",
)
```

## Concurrencia

El parámetro `concurrencia` controla las peticiones simultáneas con `asyncio.Semaphore`. El parámetro `rps` establece el rate limit global en requests por segundo. Con `concurrencia=1` (default) la descarga es secuencial.

```python
await dl.descargar(
    endpoint="matriz",
    storage="json",
    concurrencia=5,   # 5 peticiones simultáneas
    rps=3.0,          # máximo 3 requests/segundo
)
```


## Ejemplos de uso

El notebook [demo_rnpd_api.ipynb](demo_rnpd_api.ipynb) contiene ejemplos completos de cómo usar la API. Muestra todas las configuraciones disponibles, incluyendo filtros, endpoints diferentes, y opciones de descarga.

## Scripts de descarga de datos

### **descargar_por_periodo.py (RECOMENDADO)**

Divide la descarga en rangos de tiempo y guarda cada período en un archivo JSON separado en la carpeta `raw_jsons/`. Acepta las granularidades:
- mes: Descarga mes por mes (recomendado para asegurar que la descarga se termine)
- bimestre: Descarga cada 2 meses
- trimestre: Descarga cada 3 meses
- semestre: Descarga cada 6 meses


💡 **Recomendación:** Para años con muchos registros (2018-2023), es mejor usar particiones mensuales. Para años antiguos, particiones anuales son suficientes.


Estos parámetros se pueden cambiar los parámetros en la parte superior del script:

```python
ANIO = 2026
UNIDAD = "mes"              # granularidad de la descarga
MES_MAXIMO = 5              # hasta qué mes generar rangos
ENDPOINT = "matriz"         # o "lista" cuando funcione
ROWS = 10
CONCURRENCIA = 2            # peticiones simultáneas
RPS = 2                     # rate limit (requests/segundo)
CARPETA_SALIDA = "raw_jsons"
```

Y para ejecutar:

```bash
python descargar_por_periodo.py
```

Los archivos se guardan con el nombre: `raw_jsons/registros_YYYY-MM-DD_YYYY-MM-DD.json`

### **descarga_masiva.py**

⚠️**No recomendado**⚠️. Este script intenta descargar todos los registros de una sola vez. Debido a limitaciones de la API, frecuentemente falla, se queda bloqueado o devuelve respuestas incompletas. 

## Carpetas de datos

### `raw_jsons/`

Contiene archivos JSON con registros descargados usando `descargar_por_periodo.py`, organizados por período de tiempo. Los registros hasta 2025 provienen del endpoint de lista, y los de 2026 del endpoint de matriz, debido a que el endpoint de lista dejó de funcionar.


### `datos/`

Contiene los archivos CSV resultantes de procesar y consolidar los JSONs, y el CSV de totales:

- dataset_desaparecidos_con_duplicados.csv: Dataset con duplicados
- dataset_desaparecidos_sin_duplicados.csv: Dataset limpio, deduplicado por IDVictimadirecta
- records_anuales.csv: Resumen de registros agrupados por año para comparar totales anuales vs mensuales

## Consolidar JSONs

El notebook [consolidar_jsons.ipynb](consolidar_jsons.ipynb) se puede utilizar para combinar todos los archivos JSON descargados en la carpeta `raw_jsons/` en un único archivo (o en archivos CSV procesados):
1. Lee todos los JSONs de `raw_jsons/`, usando un regex configurable para buscar por nombre de archivo
2. Los combina en una lista única, quitando elementos no válidos
3. Genera dos versiones:
   - **Con duplicados**: Todas las entradas encontradas
   - **Sin duplicados**: Deduplicadas por IDVictimadirecta
4. Exporta como CSV a la carpeta `datos/`


## Almacenamiento en base de datos

### Tabla desaparecidos

Hay un archivo `sql_statement.sql` disponible con la setencia SQL para crear la tabla de volcado en MySQL. Sin embargo, **no se recomienda su uso** porque el campo `edad` veces falta en los registros, causando errores de inserción. Además, como de por sí la API es lenta, añadir la capa de inserción en base de datos ralentiza aún más el proceso. Es mejor usar el notebook [consolidar_jsons.ipynb](consolidar_jsons.ipynb) para procesar los JSONs y guardarlos como CSV.

### MySQL programático

Si se prefiere cargar directamente en MySQL desde python, puedes usar el cliente de la API:

```python
from sqlalchemy import create_engine

engine = create_engine("mysql+pymysql://user:pass@host/db")
await dl.descargar(
    endpoint="matriz",
    storage="mysql",
    engine=engine,
    table_name="desaparecidos_api",
)
```


## Estructura del proyecto

```
rnpd_downloader/
├── crypto_utils.py        # cifrado CryptoJS (AES-256-CBC)
├── api_client.py          # cliente HTTP async (aiohttp)
├── downloader.py          # orquestador de descargas
└── storage.py             # almacenamientos JSON y MySQL
raw_jsons/                 # JSONs con datos descargados
datos/                     # CSVs con/sin duplicados
consolidar_jsons.ipynb     # notebook para procesar JSONs a CSV
demo_rnpd_api.ipynb        # notebook demostrativo con ejemplos
descargar_por_periodo.py   # script de descarga recomendado
descarga_masiva.py         # script de descarga completa 
sql_statement.sql          # statement SQL
```

## Cambiar la clave fija del token

Si la clave fija usada para generar los tokens JWT cambia en la API del RNPDNO, hay actualizar la constante en [utils/crypto_utils.py](utils/crypto_utils.py):

```python
# en crypto_utils.py
_CLAVE_FIJA_TOKEN = "z427FcQwMSPZuFbIjNWGDqUpw1MEo1DG7cIOBSuI3ps"

# reemplazar con la nueva clave
_CLAVE_FIJA_TOKEN = "NUEVA_CLAVE"
```

Sin esta clave correcta, no se pueden generar tokens válidos y la API rechazará todas las peticiones.

## Planes futuros

El proyecto sigue en desarrollo. Se planea agregar:
- Método para recuperar un registro específico por `IDVictimadirecta`. Esto permitirá verificar si un registro en concreto ha sido borrado o no. Será útil para hacer seguimiento de cambios en la base de datos de manera puntual




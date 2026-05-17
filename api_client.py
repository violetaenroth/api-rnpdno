import aiohttp
import time
from datetime import datetime, timezone
from typing import Optional, Union
from loguru import logger

from utils.crypto_utils import (
    generar_url_cryptojs, generar_token_endpoint,
    verificar_token, AccionEndpoint,
)

BASE_URL = "https://apiconsultapublicarnpdno.segob.gob.mx/api"
TOKEN_URL = f"{BASE_URL}/t"


class APIClient:
    """
    Cliente async para la API de RNPDNO con manejo automático de tokens.
    Uso:
        async with APIClient() as api:
            token = await api.obtener_token()
            total = await api.obtener_total()
            datos = await api.obtener_pagina("matriz", rows=10, page=1)
    Args:
        requests_por_token: Máximo de peticiones antes de renovar token.
        token_duracion: Duración máxima del token en segundos.
    """

    def __init__(self, requests_por_token=100, token_duracion=3000):
        self.token: Optional[str] = None
        self._token_timestamp: float = 0
        self._contador: int = 0
        self._requests_por_token = requests_por_token
        self._token_duracion = token_duracion
        self._session: Optional[aiohttp.ClientSession] = None


    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cerrar(self):
        """Cierra la sesión HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()


    async def obtener_token(self) -> str:
        """Solicita un nuevo JWT a la API."""
        session = await self._get_session()
        endpoint_cifrado = generar_token_endpoint()
        url = f"{TOKEN_URL}/{endpoint_cifrado}"

        async with session.post(url, json={}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if not data.get('result', {}).get('success'):
            raise RuntimeError(f"Error obteniendo token: {data}")

        self.token = data['result']['data']
        self._token_timestamp = time.time()
        self._contador = 0
        logger.success("🔑 Token obtenido")
        return self.token

    def token_vigente(self) -> bool:
        """Verifica si el token actual sigue siendo válido."""
        if not self.token:
            return False
        if time.time() - self._token_timestamp > self._token_duracion:
            return False
        if self._contador >= self._requests_por_token:
            return False
        return True

    async def asegurar_token(self) -> str:
        """Retorna token vigente, renovando si es necesario."""
        if not self.token_vigente():
            await self.obtener_token()
        return self.token

    @staticmethod
    def _fecha_a_iso(fecha: Union[datetime, str, None]) -> Optional[str]:
        if fecha is None:
            return None
        if isinstance(fecha, str):
            return fecha
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return fecha.isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _construir_filtros(
        folio="", rango="", estado="", municipio="",
        fecha_inicio=None, fecha_fin=None) -> dict:
        fi = APIClient._fecha_a_iso(fecha_inicio)
        ff = APIClient._fecha_a_iso(fecha_fin)
        filtros = {
            "folio": folio,
            "rango": rango,
            "estado": estado,
            "municipio": municipio,
        }
        # slo incluir rango_fecha si hay fechas (JS no lo manda cuando no hay)
        if fi and ff:
            filtros["rango_fecha"] = [fi, ff]
        return filtros


    async def _post(self, accion: AccionEndpoint, filtros: dict,
                    rows=10, page=1, timeout=90) -> dict:
        """Petición POST genérica a la API con endpoint cifrado."""
        session = await self._get_session()
        token = await self.asegurar_token()
        url_cifrada = generar_url_cryptojs(accion, filtros, token)

        async with session.post(
            f"{BASE_URL}/p/{url_cifrada}",
            json={"rows": rows, "page": page},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            self._contador += 1
            resp.raise_for_status()
            return await resp.json()

    async def _get(self, accion: AccionEndpoint, filtros: dict,
                   timeout=90) -> dict:
        """Petición GET genérica a la API con endpoint cifrado (sin payload)."""
        session = await self._get_session()
        token = await self.asegurar_token()
        url_cifrada = generar_url_cryptojs(accion, filtros, token)

        async with session.get(
            f"{BASE_URL}/p/{url_cifrada}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            self._contador += 1
            resp.raise_for_status()
            return await resp.json()

    async def obtener_total(self, **filtros_kwargs) -> int:
        """
        Obtiene el total de registros que coinciden con los filtros.
        kwargs: folio, rango, estado, municipio, fecha_inicio, fecha_fin
        """
        filtros = self._construir_filtros(**filtros_kwargs)
        data = await self._post(AccionEndpoint.GET_PAGINADOR, filtros)
        return data['result']['data']

    async def obtener_municipios(self, id_estado: str) -> list:
        """
        Obtiene la lista de municipios de un estado.
        Args:
            id_estado: ID del estado como string (ej: "14" para Jalisco).
        """
        session = await self._get_session()
        token = await self.asegurar_token()
        url_cifrada = generar_url_cryptojs(
            AccionEndpoint.MUNICIPIOS, {"id": id_estado}, token
        )
        async with session.post(
            f"{BASE_URL}/p/{url_cifrada}",
            json={"id": id_estado},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            self._contador += 1
            resp.raise_for_status()
            data = await resp.json()
        return data.get("result", {}).get("data", [])

    async def obtener_pagina(
        self, endpoint: str = "matriz",
        rows=10, page=1, **filtros_kwargs) -> list:
        """
        Descarga una sola página de datos.
        Args:
            endpoint: "matriz" o "lista"
            rows: Registros por página.
            page: Número de página.
            **filtros_kwargs: folio, rango, estado, municipio, fecha_inicio, fecha_fin
        """
        accion = (AccionEndpoint.GET_INFO_MATRIZ if endpoint == "matriz"
                  else AccionEndpoint.GET_INFO_LISTA)
        filtros = self._construir_filtros(**filtros_kwargs)
        data = await self._post(accion, filtros, rows=rows, page=page)
        return data.get("result", {}).get("data", {}).get("data", [])

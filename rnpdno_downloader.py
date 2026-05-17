import asyncio
import time
from typing import Optional
from loguru import logger

from api_client import APIClient
from storage import StorageBackend, JSONStorage, MySQLStorage


class RNPDNODownloader:
    """
    Descargador async de datos de RNPDNO.
    Soporta:
      - Endpoints: get_info_matriz o get_info_lista
      - Filtros opcionales (estado, municipio, folio, fechas)
      - Almacenamiento en JSON local o MySQL
      - Concurrencia + rate limiting
      - Lectura del total de registros por separado
    """

    def __init__(self, requests_por_token=100, token_duracion=3000):
        self.api = APIClient(
            requests_por_token=requests_por_token,
            token_duracion=token_duracion)

    async def __aenter__(self):
        await self.api.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.api.__aexit__(*exc)

    async def cerrar(self):
        """Cierra la sesión HTTP"""
        await self.api.cerrar()


    async def obtener_total(self, **filtros) -> int:
        """
        Obtiene el total de registros que coinciden con los filtros.
        Kwargs: estado, municipio, fecha_inicio, fecha_fin
        """
        total = await self.api.obtener_total(**filtros)
        logger.info(f"Total de registros: {total:,}")
        return total

    async def obtener_municipios(self, id_estado: int) -> list:
        """
        Obtiene municipios de un estado.
        Args:
            id_estado: ID numérico del estado (1-32)
        """
        return await self.api.obtener_municipios(id_estado)

    async def descargar(
        self,
        endpoint: str = "matriz",
        storage: str = "json",
        rows: int = 10,
        max_paginas: Optional[int] = None,
        concurrencia: int = 1,
        rps: float = 2.0,
        # storage kwargs
        filepath: str = "datos_rnpd.json",
        engine=None,
        table_name: str = "desaparecidos_api",
        batch_size: int = 1000,
        **filtros) -> list:
        """
        Descarga datos de la API.
        Args:
            endpoint: "matriz" o "lista"
            storage: "json" o "mysql"
            rows: Registros por página (default 10)
            max_paginas: Límite de páginas, None = todas
            concurrencia: Peticiones simultáneas, 1 = secuencial
            rps: Requests por segundo (rate limit global)
            filepath: Ruta del JSON (solo si storage="json")
            engine: SQLAlchemy engine (solo si storage="mysql")
            table_name: Nombre de tabla (solo si storage="mysql")
            batch_size: Registros por batch de escritura
            **filtros: estado, municipio, fecha_inicio, fecha_fin
        Returns:
            Lista con todos los registros descargados.
        """
        if endpoint not in ("matriz", "lista"):
            raise ValueError("endpoint debe ser 'matriz' o 'lista'")

        backend = self._crear_storage(storage, filepath, engine, table_name, batch_size)

        # obtener el total para calcular página máxima
        total = await self.api.obtener_total(**filtros)

        if not isinstance(total, int):
            logger.error("Error al calcular total de páginas. Verifica los filtros.")
            logger.debug(f"Respuesta total: {total}")
            raise ValueError("Total no es un entero, no se puede calcular páginas")
        else:
            total_paginas = (total + rows - 1) // rows
     

        if max_paginas:
            total_paginas = min(total_paginas, max_paginas)

        logger.info(
            f"Descargando {total:,} registros ({total_paginas:,} páginas) "
            f"| endpoint={endpoint} | concurrencia={concurrencia} | rps={rps}"
        )

        todos = []
        contadores = {"exitosas": 0, "errores": 0}
        inicio = time.time()

        # semáforo para limitar concurrencia + intervalo para rate limiting
        semaforo = asyncio.Semaphore(concurrencia)
        intervalo = 1.0 / rps

        async def procesar_pagina(page: int):
            """Descarga una página con semáforo, rate limit y reintentos."""
            async with semaforo:
                await asyncio.sleep(intervalo)  # rate limiting simple
                datos = await self._pagina_con_retry(endpoint, rows, page, filtros)

            if datos:
                todos.extend(datos)
                backend.guardar(datos)
                contadores["exitosas"] += 1

                if contadores["exitosas"] % 50 == 0:
                    self._log_progreso(
                        contadores["exitosas"], total_paginas,
                        len(todos), inicio
                    )
            else:
                contadores["errores"] += 1

            # circuit breaker
            if contadores["errores"] > 50 and contadores["exitosas"] < 10:
                raise RuntimeError("Demasiados errores iniciales, abortando")

        # lanzar todas las tareas
        tareas = [procesar_pagina(p) for p in range(1, total_paginas + 1)]

        try:
            await asyncio.gather(*tareas, return_exceptions=True)
        except RuntimeError as e:
            logger.error(str(e))

        backend.flush()

        
        minutos = (time.time() - inicio) / 60
        vel = total_paginas / minutos if minutos > 0 else 0
        logger.success(
            f"Descarga terminada en {minutos:.1f} min | "
            f"{len(todos):,} registros | {vel:.0f} pág/min | "
            f"Errores: {contadores['errores']}"
        )
        logger.success(f"{backend.total_guardados():,} registros guardados")
        return todos


    async def _pagina_con_retry(self, endpoint, rows, page, filtros,
                                 max_reintentos=3) -> list | None:
        for intento in range(max_reintentos):
            try:
                return await self.api.obtener_pagina(
                    endpoint=endpoint, rows=rows, page=page, **filtros
                )
            except Exception as e:
                error_str = str(e)
                wait = 2 ** intento

                if '401' in error_str:
                    logger.warning(f"Página {page}: Token expirado, renovando...")
                    await self.api.obtener_token()
                    wait = 1
                elif '429' in error_str:
                    wait = 5 * (2 ** intento)
                    logger.warning(f"Página {page}: Rate limit, esperando {wait}s")

                if intento < max_reintentos - 1:
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Página {page}: falló tras {max_reintentos} intentos")
                    return None


    @staticmethod
    def _crear_storage(tipo, filepath, engine, table_name, batch_size) -> StorageBackend:
        if tipo == "json":
            return JSONStorage(filepath=filepath, batch_size=batch_size)
        elif tipo == "mysql":
            if engine is None:
                raise ValueError("Se requiere 'engine' para storage='mysql'")
            return MySQLStorage(engine=engine, table_name=table_name, batch_size=batch_size)
        else:
            raise ValueError(f"Storage '{tipo}' no soportado. Usa 'json' o 'mysql'")



    @staticmethod
    def _log_progreso(paginas_ok, total_paginas, registros, inicio):
        transcurrido = time.time() - inicio
        if transcurrido == 0:
            return
        velocidad = paginas_ok / transcurrido * 60
        eta = (total_paginas - paginas_ok) / (paginas_ok / transcurrido) / 60
        progreso = (paginas_ok / total_paginas) * 100
        logger.info(
            f"{paginas_ok:,}/{total_paginas:,} ({progreso:.1f}%) | "
            f"Registros: {registros:,} | {velocidad:.0f} pág/min | ETA: {eta:.1f} min"
        )

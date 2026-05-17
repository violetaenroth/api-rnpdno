import asyncio
import json
import os
import time
from dataclasses import dataclass, asdict
from loguru import logger

from rnpdno_downloader import RNPDNODownloader
logger.add("log/descarga_masiva.log", rotation="100 MB", level="INFO")


ENDPOINT = "matriz"            
ROWS = 10 # registros por página
CONCURRENCIA = 5 # peticiones simultáneas
RPS = 2.0                       
REGISTROS_POR_CHECKPOINT = 2500 # guardar checkpoint cada n registros
CARPETA = "checkpoints"         

# dejar vacío para descargar todo (no sirve, pero por si se quiere probar de todas maneras)
FILTROS = {
    # "fecha_inicio": "2024-01-01T00:00:00.000Z",
    # "fecha_fin": "2024-12-31T23:59:59.999Z",
}


@dataclass
class EstadoDescarga:
    total_registros: int = 0
    total_paginas: int = 0
    ultima_pagina: int = 0 # última página completada
    registros_descargados: int = 0
    checkpoint_actual: int = 0
    completado: bool = False

    def guardar(self, carpeta: str):
        path = os.path.join(carpeta, "estado.json")
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def cargar(cls, carpeta: str) -> "EstadoDescarga":
        path = os.path.join(carpeta, "estado.json")
        if not os.path.exists(path):
            return cls()
        with open(path, "r") as f:
            return cls(**json.load(f))


def guardar_checkpoint(datos: list, numero: int, carpeta: str):
    path = os.path.join(carpeta, f"checkpoint_{numero:05d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    logger.info(f"Checkpoint {numero} guardado: {len(datos):,} registros -> {path}")


async def descarga_masiva():
    os.makedirs(CARPETA, exist_ok=True)

    # cargar estado previo si existe
    estado = EstadoDescarga.cargar(CARPETA)

    if estado.completado:
        logger.info(
            f"Descarga ya completada ({estado.registros_descargados:,} registros "
            f"en {estado.checkpoint_actual} checkpoints)"
        )
        return

    async with RNPDNODownloader() as dl:
        total = await dl.obtener_total(**FILTROS)
        total_paginas = (total + ROWS - 1) // ROWS

        estado.total_registros = total
        estado.total_paginas = total_paginas

        pagina_inicio = estado.ultima_pagina + 1
        paginas_restantes = total_paginas - estado.ultima_pagina

        if pagina_inicio > 1:
            logger.info(
                f"Retomando desde página {pagina_inicio:,} "
                f"({estado.registros_descargados:,} registros previos)"
            )

        logger.info(
            f"Total: {total:,} registros | {total_paginas:,} páginas | "
            f"Pendientes: {paginas_restantes:,} páginas"
        )

        # buffer para acumular registros hasta el siguiente checkpoint
        buffer = []
        paginas_ok = 0
        registros_en_bloque = 0 
        errores = 0
        inicio = time.time()

        semaforo = asyncio.Semaphore(CONCURRENCIA)
        intervalo = 1.0 / RPS

        paginas_por_checkpoint = REGISTROS_POR_CHECKPOINT // ROWS

        def _log_progreso():
            transcurrido = time.time() - inicio
            if transcurrido == 0 or paginas_ok == 0:
                return
            vel = paginas_ok / transcurrido * 60
            total_completadas = estado.ultima_pagina + paginas_ok
            pags_restantes = total_paginas - total_completadas
            eta = pags_restantes / (paginas_ok / transcurrido) / 60
            progreso = (total_completadas / total_paginas) * 100
            logger.info(
                f"Progreso: {total_completadas:,}/{total_paginas:,} ({progreso:.1f}%) | "
                f"{estado.registros_descargados + registros_en_bloque:,} registros | "
                f"{vel:.0f} pág/min | ETA: {eta:.1f} min | "
                f"Errores: {errores}"
            )

        for bloque_inicio in range(pagina_inicio, total_paginas + 1, paginas_por_checkpoint):
            bloque_fin = min(bloque_inicio + paginas_por_checkpoint, total_paginas + 1)
            paginas_bloque = list(range(bloque_inicio, bloque_fin))
            registros_en_bloque = 0

            logger.info(
                f"Bloque: páginas {bloque_inicio:,}-{bloque_fin - 1:,} "
                f"({len(paginas_bloque)} páginas)"
            )

            async def descargar_pagina(page: int):
                nonlocal paginas_ok, errores, registros_en_bloque
                async with semaforo:
                    await asyncio.sleep(intervalo)
                    for intento in range(3):
                        try:
                            datos = await dl.api.obtener_pagina(
                                endpoint=ENDPOINT, rows=ROWS, page=page, **FILTROS
                            )
                            paginas_ok += 1
                            registros_en_bloque += len(datos) if datos else 0
                            if paginas_ok % 50 == 0:
                                _log_progreso()
                            return page, datos
                        except Exception as e:
                            err = str(e)
                            if "401" in err:
                                logger.warning(f"Página {page}: token expirado, renovando...")
                                await dl.api.obtener_token()
                                await asyncio.sleep(1)
                            elif "429" in err:
                                wait = 5 * (2 ** intento)
                                logger.warning(f"Página {page}: rate limit, esperando {wait}s")
                                await asyncio.sleep(wait)
                            else:
                                await asyncio.sleep(2 ** intento)
                            if intento == 2:
                                errores += 1
                                logger.error(f"Página {page}: falló tras 3 intentos")
                                return page, None

            tareas = [descargar_pagina(p) for p in paginas_bloque]
            resultados_raw = await asyncio.gather(*tareas)

            # mantener orden de página
            for page, datos in sorted(resultados_raw, key=lambda x: x[0]):
                if datos:
                    buffer.extend(datos)

            # guardar checkpoint
            if buffer:
                estado.checkpoint_actual += 1
                guardar_checkpoint(buffer, estado.checkpoint_actual, CARPETA)

                estado.ultima_pagina = bloque_fin - 1
                estado.registros_descargados += len(buffer)
                estado.guardar(CARPETA)

                buffer = []

            # circuit breaker
            if errores > 100 and paginas_ok < 20:
                logger.error("Demasiados errores, abortando")
                estado.guardar(CARPETA)
                return

        # marcar como completado
        estado.completado = True
        estado.guardar(CARPETA)

        minutos = (time.time() - inicio) / 60
        logger.success(
            f"Descarga completada en {minutos:.1f} min | "
            f"{estado.registros_descargados:,} registros | "
            f"{estado.checkpoint_actual} checkpoints | "
            f"Errores: {errores}"
        )

if __name__ == "__main__":
    asyncio.run(descarga_masiva())

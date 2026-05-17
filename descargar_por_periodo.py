import asyncio
from datetime import datetime
import time
from calendar import monthrange
from typing import List, Tuple, Literal
from loguru import logger
from rnpdno_downloader import RNPDNODownloader

ANIO = 2026
UNIDAD = "mes"              # "mes", "bimestre", "trimestre", "semestre"
MES_MAXIMO = 5              # hasta qué mes generar rangos (1-12)
ENDPOINT = "matriz"         # "matriz" o "lista"
ROWS = 10
CONCURRENCIA = 2
RPS = 2
CARPETA_SALIDA = "raw_jsons"    
ESTADO = ""     
MUNICIPIO = ""   


def _iso_inicio(anio: int, mes: int) -> str:
    return f"{anio}-{mes:02d}-01T00:00:00.000Z"


def _iso_fin(anio: int, mes: int) -> str:
    ultimo_dia = monthrange(anio, mes)[1]
    return f"{anio}-{mes:02d}-{ultimo_dia:02d}T23:59:59.999Z"


def generar_rangos(
    anio: int,
    unidad: Literal["mes", "bimestre", "trimestre", "semestre"] = "mes",
    mes_maximo: int = 12
    ) -> List[Tuple[str, str]]:
    """
    Genera lista de tuplas (fecha_inicio, fecha_fin) en formato ISO.
    Args:
        anio: Año para generar los rangos.
        unidad: Granularidad del rango.
        mes_maximo: Mes máximo hasta el cual generar rangos (default: 12).
    Returns:
        Lista de tuplas (inicio_iso, fin_iso).
    """
    meses_por_unidad = {
        "mes": 1,
        "bimestre": 2,
        "trimestre": 3,
        "semestre": 6
        }

    if unidad not in meses_por_unidad:
        raise ValueError(f"Unidad '{unidad}' no válida. Usa: {list(meses_por_unidad.keys())}")

    if not 1 <= mes_maximo <= 12:
        raise ValueError(f"mes_maximo debe estar entre 1 y 12, recibido: {mes_maximo}")

    paso = meses_por_unidad[unidad]
    rangos = []

    for mes_inicio in range(1, mes_maximo + 1, paso):
        mes_fin = min(mes_inicio + paso - 1, mes_maximo)
        rangos.append((_iso_inicio(anio, mes_inicio), _iso_fin(anio, mes_fin)))

    return rangos


def generar_rangos_multi_anio(
    anio_inicio: int,
    anio_fin: int,
    unidad: Literal["mes", "bimestre", "trimestre", "semestre"] = "mes"
    ) -> List[Tuple[str, str]]:
    rangos = []
    for anio in range(anio_inicio, anio_fin + 1):
        rangos.extend(generar_rangos(anio, unidad))
    return rangos


def nombre_archivo(fecha_inicio: str, fecha_fin: str, carpeta: str = ".") -> str:
    inicio = fecha_inicio[:10]
    fin = fecha_fin[:10]        
    return f"{carpeta}/registros_{inicio}_{fin}.json"


async def descargar_por_periodos(
    anio: int = ANIO,
    unidad: str = UNIDAD,
    endpoint: str = ENDPOINT,
    rows: int = ROWS,
    concurrencia: int = CONCURRENCIA,
    rps: float = RPS,
    carpeta: str = CARPETA_SALIDA,
    estado: str = ESTADO,
    municipio: str = MUNICIPIO,
    MES_MAXIMO: int = MES_MAXIMO):
    """Descarga datos divididos por periodos de tiempo."""

    rangos = generar_rangos(anio, unidad, mes_maximo=MES_MAXIMO)

    logger.info(
        f"{len(rangos)} periodos ({unidad}) para {anio} "
        f"| endpoint={endpoint} | concurrencia={concurrencia}"
    )


    import os
    os.makedirs(carpeta, exist_ok=True)

    async with RNPDNODownloader() as dl:
        total_global = 0

        for i, (inicio, fin) in enumerate(rangos, 1):
            filepath = nombre_archivo(inicio, fin, carpeta)
            logger.info(f"Periodo {i}/{len(rangos)}: {inicio[:10]} → {fin[:10]}")
            logger.info(f"Archivo: {filepath}")

            filtros = {"fecha_inicio": inicio, "fecha_fin": fin}
            if estado:
                filtros["estado"] = estado
            if municipio:
                filtros["municipio"] = municipio

            datos = await dl.descargar(
                endpoint=endpoint,
                storage="json",
                filepath=filepath,
                rows=rows,
                concurrencia=concurrencia,
                rps=rps,
                **filtros,
            )

            total_global += len(datos)
            logger.info(f"{len(datos):,} registros → {filepath}")

            time.sleep(180)  # pausa entre periodos para evitar sobrecarga

        logger.success(f"\nDescarga completa: {total_global:,} registros en {len(rangos)} archivos")


if __name__ == "__main__":
    asyncio.run(descargar_por_periodos())

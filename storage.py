import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict
from loguru import logger


class StorageBackend(ABC):
    """Interfaz base para backends de almacenamiento."""

    @abstractmethod
    def guardar(self, registros: List[Dict]):
        """Guarda una lista de registros."""

    @abstractmethod
    def flush(self):
        """Fuerza escritura de datos pendientes."""

    @abstractmethod
    def total_guardados(self) -> int:
        """Retorna cuántos registros se han guardado."""



class JSONStorage(StorageBackend):
    """
    Guarda registros en un archivo json local.
    Escribe al disco cada `batch_size` registros o al hacer flush().
    """

    def __init__(self, filepath: str = "datos_rnpd.json", batch_size: int = 1000):
        self.filepath = filepath
        self.batch_size = batch_size
        self._buffer: List[Dict] = []
        self._total: int = 0

        # cargar datos (si el archivo ya existe)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                self._total = len(existing)
                logger.info(f"Archivo existente con {self._total:,} registros")
            except (json.JSONDecodeError, Exception):
                logger.warning(f"No se pudo leer {filepath}, se sobreescribirá")

    def guardar(self, registros: List[Dict]):
        self._buffer.extend(registros)
        if len(self._buffer) >= self.batch_size:
            self._escribir()

    def flush(self):
        if self._buffer:
            self._escribir()

    def _escribir(self):
        """Append al archivo JSON. Si ya existe, carga, concatena y reescribe."""
        
        existentes = []
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    existentes = json.load(f)
            except (json.JSONDecodeError, Exception):
                pass

        existentes.extend(self._buffer)
        with open(self.filepath, 'w', encoding='utf-8') as f:
            json.dump(existentes, f, ensure_ascii=False, default=str)

        self._total += len(self._buffer)
        logger.debug(f"{len(self._buffer)} registros escritos a {self.filepath}")
        self._buffer = []

    def total_guardados(self) -> int:
        return self._total



class MySQLStorage(StorageBackend):
    """
    Inserta registros en una tabla MySQL existente.
    !IMPORTANTE!: La tabla debe existir previamente con la estructura correcta.
    Todas las columnas (excepto id_interno y fecha_insercion) deben ser VARCHAR NULL.
    Args:
        engine: SQLAlchemy engine ya conectado.
        table_name: Nombre de la tabla destino.
        batch_size: Registros por INSERT.
    """

    def __init__(self, engine, table_name: str, batch_size: int = 1000):
        from sqlalchemy import MetaData, Table
        self._engine = engine
        self._table_name = table_name
        self._batch_size = batch_size
        self._buffer: List[Dict] = []
        self._total: int = 0

        # verificar que la tabla existe al inicializar
        metadata = MetaData()
        self._table = Table(table_name, metadata, autoload_with=engine)
        self._valid_columns = {col.name for col in self._table.columns}
        logger.info(f"Tabla '{table_name}' encontrada ({len(self._valid_columns)} columnas)")

    def guardar(self, registros: List[Dict]):
        self._buffer.extend(registros)
        if len(self._buffer) >= self._batch_size:
            self._insertar()

    def flush(self):
        if self._buffer:
            self._insertar()

    def _insertar(self):
        from sqlalchemy.dialects.mysql import insert

        now = datetime.now()
        normalized = []
        for row in self._buffer:
            if isinstance(row, str):
                try:
                    row = json.loads(row)
                except Exception:
                    continue
            if not isinstance(row, dict):
                continue

            norm = {'fecha_insercion': now}
            for col in self._valid_columns:
                if col in ('id_interno', 'fecha_insercion'):
                    continue
                val = row.get(col)
                if val is not None:
                    norm[col] = str(val).strip() or None
                else:
                    norm[col] = None
            normalized.append(norm)

        if not normalized:
            self._buffer = []
            return

        inserted = 0
        failed = 0
        try:
            with self._engine.begin() as conn:
                for i in range(0, len(normalized), self._batch_size):
                    batch = normalized[i:i + self._batch_size]
                    try:
                        conn.execute(insert(self._table).values(batch))
                        inserted += len(batch)
                    except Exception:
                        for single in batch:
                            try:
                                conn.execute(insert(self._table).values([single]))
                                inserted += 1
                            except Exception:
                                failed += 1
        except Exception as e:
            logger.error(f"Error en inserción: {e}")

        self._total += inserted
        if failed:
            logger.warning(f"{failed} filas fallaron")
        logger.debug(f"{inserted} registros insertados en '{self._table_name}'")
        self._buffer = []

    def total_guardados(self) -> int:
        return self._total

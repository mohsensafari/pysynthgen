"""Parquet sink — writes rows via pyarrow (optional dependency)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pysynthgen.sinks.base import BaseSink, Row


class ParquetSink(BaseSink):
    """Writes rows as a Parquet file.

    The Arrow schema is inferred from the first batch and reused for the rest, so
    later batches are cast to that schema (every column is nullable, so
    ``null_probability`` fields work). Requires ``pyarrow`` — install
    ``pysynthgen[parquet]``.
    """

    def __init__(self, path: str | Path) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - exercised via install extras
            raise ImportError(
                "ParquetSink requires 'pyarrow'. Install it with: pip install pysynthgen[parquet]"
            ) from exc
        self.path = Path(path)
        self._pa = pa
        self._pq = pq
        self._schema: Any = None
        self._writer: Any = None

    def write_batch(self, rows: list[Row]) -> None:
        if not rows:
            return
        if self._schema is None:
            table = self._pa.Table.from_pylist(rows)
            self._schema = table.schema
            self._writer = self._pq.ParquetWriter(str(self.path), self._schema)
        else:
            table = self._pa.Table.from_pylist(rows, schema=self._schema)
        self._writer.write_table(table)

    def finalize(self) -> str:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        elif not self.path.exists():
            self.path.touch()
        return str(self.path)

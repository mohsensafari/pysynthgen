"""Parquet sink — writes rows via pyarrow (optional dependency)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pysynthgen.sinks.base import BaseSink, Row

# Declared precision for decimal columns; the widest decimal128 allows. Arrow
# infers a decimal's precision from the values it is shown, so the first batch
# would otherwise fix a precision that a larger value in a later batch could not
# be cast into. Widening costs nothing: Parquet stores decimal128 in a fixed 16
# bytes whatever the declared precision.
_DECIMAL_PRECISION = 38


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

    def _widen_decimals(self, schema: Any) -> Any:
        """Re-declare every inferred decimal column at the maximum precision."""
        pa = self._pa
        fields = [
            f.with_type(pa.decimal128(_DECIMAL_PRECISION, f.type.scale))
            if pa.types.is_decimal(f.type)
            else f
            for f in schema
        ]
        return pa.schema(fields)

    def write_batch(self, rows: list[Row]) -> None:
        if not rows:
            return
        if self._schema is None:
            self._schema = self._widen_decimals(self._pa.Table.from_pylist(rows).schema)
            self._writer = self._pq.ParquetWriter(str(self.path), self._schema)
        table = self._pa.Table.from_pylist(rows, schema=self._schema)
        self._writer.write_table(table)

    def finalize(self) -> str:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        elif not self.path.exists():
            self.path.touch()
        return str(self.path)

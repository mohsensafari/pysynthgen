"""Avro sink — writes rows via fastavro (optional dependency).

The Avro schema is inferred from the first batch, with every field typed as a
union of ``null`` and its value type so ``null_probability`` fields work. Naive
datetimes are written as UTC so output is deterministic regardless of the host's
local timezone.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import IO, Any

from synthgen.sinks.base import BaseSink, Row


def _avro_type(value: Any) -> Any:
    # bool before int (bool is an int subclass); datetime before date likewise.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, datetime):
        return {"type": "long", "logicalType": "timestamp-micros"}
    if isinstance(value, date):
        return {"type": "int", "logicalType": "date"}
    if isinstance(value, bytes):
        return "bytes"
    return "string"


def _first_non_null(rows: list[Row], key: str) -> Any:
    for row in rows:
        if row.get(key) is not None:
            return row[key]
    return None


def _infer_schema(rows: list[Row]) -> dict[str, Any]:
    fields = [
        {"name": key, "type": ["null", _avro_type(_first_non_null(rows, key))], "default": None}
        for key in rows[0]
    ]
    return {"type": "record", "name": "SynthgenRow", "fields": fields}


def _avro_row(row: Row) -> Row:
    out: Row = {}
    for key, value in row.items():
        # datetime is a date subclass; only tz-normalize actual datetimes.
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        out[key] = value
    return out


class AvroSink(BaseSink):
    """Writes rows as an Avro container file. Requires ``fastavro`` — install
    ``synthgen[avro]``."""

    def __init__(self, path: str | Path) -> None:
        try:
            from fastavro.write import Writer
        except ImportError as exc:  # pragma: no cover - exercised via install extras
            raise ImportError(
                "AvroSink requires 'fastavro'. Install it with: pip install synthgen[avro]"
            ) from exc
        self.path = Path(path)
        self._Writer = Writer
        self._file: IO[bytes] | None = None
        self._writer: Any = None

    def write_batch(self, rows: list[Row]) -> None:
        if not rows:
            return
        if self._writer is None:
            self._file = self.path.open("wb")
            self._writer = self._Writer(self._file, _infer_schema(rows))
        for row in rows:
            self._writer.write(_avro_row(row))

    def finalize(self) -> str:
        if self._writer is not None:
            self._writer.flush()
            assert self._file is not None
            self._file.close()
            self._writer = None
            self._file = None
        elif not self.path.exists():
            self.path.touch()
        return str(self.path)

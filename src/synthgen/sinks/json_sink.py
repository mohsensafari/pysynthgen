"""JSON sink — writes a single JSON array, streamed row by row."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

from synthgen.sinks.base import BaseSink, Row


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__} to JSON")


class JsonSink(BaseSink):
    """Writes rows as one JSON array. The array is built incrementally, so no more
    than a single batch is held in memory at a time."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._file: IO[str] | None = None
        self._empty = True

    def write_batch(self, rows: list[Row]) -> None:
        if self._file is None:
            self._file = self.path.open("w", encoding="utf-8")
            self._file.write("[")
        for row in rows:
            self._file.write(",\n  " if not self._empty else "\n  ")
            self._empty = False
            self._file.write(json.dumps(row, default=_json_default))

    def finalize(self) -> str:
        if self._file is None:
            self.path.write_text("[]\n", encoding="utf-8")
        else:
            self._file.write("]\n" if self._empty else "\n]\n")
            self._file.close()
            self._file = None
        return str(self.path)

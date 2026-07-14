"""CSV sink — writes rows with a header inferred from the first batch."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

from synthgen.sinks.base import BaseSink, Row


def _csv_value(value: Any) -> Any:
    # Dates/datetimes go out as ISO strings; None becomes an empty cell (csv default).
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class CsvSink(BaseSink):
    """Writes rows as CSV. Column order and header come from the first row seen.

    ``delimiter`` and ``quotechar`` control the dialect; they default to the
    standard comma-separated, double-quoted format.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        delimiter: str = ",",
        quotechar: str = '"',
    ) -> None:
        self.path = Path(path)
        self.delimiter = delimiter
        self.quotechar = quotechar
        self._file: IO[str] | None = None
        self._writer: csv.DictWriter[str] | None = None

    def write_batch(self, rows: list[Row]) -> None:
        if not rows:
            return
        if self._writer is None:
            self._file = self.path.open("w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(
                self._file,
                fieldnames=list(rows[0].keys()),
                delimiter=self.delimiter,
                quotechar=self.quotechar,
            )
            self._writer.writeheader()
        for row in rows:
            self._writer.writerow({k: _csv_value(v) for k, v in row.items()})

    def finalize(self) -> str:
        if self._file is None:
            self.path.write_text("", encoding="utf-8")
        else:
            self._file.close()
            self._file = None
        return str(self.path)

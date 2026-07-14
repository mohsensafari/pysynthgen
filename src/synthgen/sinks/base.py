"""Sink interface: where generated rows are written.

A sink consumes rows in batches and produces a single output artifact. The engine
stays sink-agnostic — it hands batches to whatever sink the caller supplies — and
the sink owns the file format, resource lifecycle, and final location.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

# A generated row: field name -> value.
Row = dict[str, Any]

DEFAULT_BATCH_SIZE = 500


class BaseSink(ABC):
    """Consumes batches of rows and finalizes to a single output location.

    Lifecycle: call :meth:`write_batch` any number of times (including zero), then
    :meth:`finalize` exactly once to flush, close resources, and get the output path.
    Sinks open their underlying file lazily on the first non-empty batch.

    :meth:`write` is the one-shot convenience over that lifecycle: hand it an
    iterator of rows and it batches through :meth:`write_batch`, then finalizes.
    """

    @abstractmethod
    def write_batch(self, rows: list[Row]) -> None:
        """Append a batch of rows to the output."""

    @abstractmethod
    def finalize(self) -> str:
        """Flush and close the output, returning its path/URI."""

    def write(self, rows: Iterable[Row], batch_size: int = DEFAULT_BATCH_SIZE) -> str:
        """Write every row from ``rows`` in chunks of ``batch_size``, then finalize.

        Works with any iterable — including a lazy generator like
        ``SynthEngine.iter_rows()`` — so the full dataset is never held in memory at
        once. Each chunk is handed to :meth:`write_batch`. Returns the output path.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        batch: list[Row] = []
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                self.write_batch(batch)
                batch = []
        if batch:
            self.write_batch(batch)
        return self.finalize()

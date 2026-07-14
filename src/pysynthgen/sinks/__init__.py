"""Output sinks and the format factory.

``build_sink`` picks a sink by format string so callers (e.g. the CLI) can stay
format-agnostic. The parquet and avro sinks import their heavy dependencies lazily,
so importing this package never requires them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pysynthgen.sinks.avro_sink import AvroSink
from pysynthgen.sinks.base import BaseSink, Row
from pysynthgen.sinks.csv_sink import CsvSink
from pysynthgen.sinks.json_sink import JsonSink
from pysynthgen.sinks.parquet_sink import ParquetSink

# Factory callables: each takes a path plus format-specific keyword options.
SINK_REGISTRY: dict[str, Callable[..., BaseSink]] = {
    "json": JsonSink,
    "csv": CsvSink,
    "parquet": ParquetSink,
    "avro": AvroSink,
}

# Map common file extensions to a format, for convenience.
_EXTENSIONS = {
    ".json": "json",
    ".csv": "csv",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".avro": "avro",
}


def build_sink(fmt: str, path: str | Path, **kwargs: Any) -> BaseSink:
    """Instantiate the sink registered for ``fmt`` (e.g. ``"parquet"``)."""
    try:
        cls = SINK_REGISTRY[fmt]
    except KeyError:
        raise ValueError(
            f"unknown sink format {fmt!r}; choose from {sorted(SINK_REGISTRY)}"
        ) from None
    return cls(path, **kwargs)


def format_from_path(path: str | Path) -> str:
    """Infer a sink format from a file extension, or raise if unrecognized."""
    suffix = Path(path).suffix.lower()
    try:
        return _EXTENSIONS[suffix]
    except KeyError:
        raise ValueError(
            f"cannot infer format from extension {suffix!r}; "
            f"pass an explicit format ({sorted(SINK_REGISTRY)})"
        ) from None


__all__ = [
    "BaseSink",
    "Row",
    "JsonSink",
    "CsvSink",
    "ParquetSink",
    "AvroSink",
    "SINK_REGISTRY",
    "build_sink",
    "format_from_path",
]

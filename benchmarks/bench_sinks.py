"""Sink throughput + memory benchmark.

Generates a wide dataset and streams it into each sink format, recording wall time,
throughput, output file size, and *peak resident memory* sampled while writing. The
point is to show that memory stays flat as row count grows — the engine yields rows
lazily and sinks write in batches, so nothing holds the whole dataset at once.

Run a quick pass:

    python benchmarks/bench_sinks.py --rows 200000

Run the full target (~10M rows, multi-GB per format — this takes a while):

    python benchmarks/bench_sinks.py --rows 10000000 --outdir /path/with/space

The template below is intentionally wide (~18 columns) so each row is several
hundred bytes; at 10M rows that lands in the multi-GB range per format (exact size
varies — parquet compresses, JSON is verbose).
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# Import the optional heavy deps up front (if present) so the memory baseline is
# stable and not inflated mid-run by a lazy import.
try:
    import pyarrow  # noqa: F401
except ImportError:
    pass
try:
    import fastavro  # noqa: F401
except ImportError:
    pass

from synthgen import SynthEngine, build_sink, load_and_validate_template

ALL_FORMATS = ["json", "csv", "parquet", "avro"]


# Long text values used by "blob" category fields. Categories are cheap to draw
# (numpy returns an index), so this is how the benchmark reaches several-hundred-byte
# rows without the per-char cost of the regex/faker generators — which keeps the
# benchmark measuring sink I/O and memory rather than generator speed.
_BLOB = [
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis",
    "the quick brown fox jumps over the lazy dog while nine hungry pangolins quietly "
    "assemble a flat pack bookshelf using only a spoon and boundless determination",
    "synthetic data streams flow through batched sinks landing as parquet avro csv and "
    "json without ever buffering the entire dataset in memory at any single moment",
]


def wide_template(rows: int, seed: int = 42) -> dict[str, Any]:
    """A wide, generation-cheap template producing several-hundred-byte rows."""
    return {
        "row_count": rows,
        "seed": seed,
        "fields": [
            {"name": "id", "type": "uuid"},
            {"name": "session_id", "type": "uuid"},
            {"name": "created_at", "type": "datetime",
             "start": "2020-01-01T00:00:00", "end": "2026-01-01T00:00:00"},
            {"name": "updated_at", "type": "datetime",
             "start": "2020-01-01T00:00:00", "end": "2026-01-01T00:00:00"},
            {"name": "signup_day", "type": "date", "start": "2018-01-01", "end": "2026-01-01"},
            {"name": "age", "type": "int", "distribution": "normal",
             "mean": 38, "stddev": 12, "min": 18, "max": 95},
            {"name": "tenure_days", "type": "int", "min": 0, "max": 3650},
            {"name": "score", "type": "float", "min": 0.0, "max": 1.0},
            {"name": "balance", "type": "float", "distribution": "normal",
             "mean": 500.0, "stddev": 250.0},
            {"name": "country", "type": "category",
             "values": ["US", "NL", "DE", "FR", "GB", "ES", "IT", "SE"]},
            {"name": "plan", "type": "category", "values": ["free", "pro", "enterprise"]},
            {"name": "status", "type": "category", "values": ["active", "trial", "churned"]},
            {"name": "bio", "type": "category", "values": _BLOB},
            {"name": "notes", "type": "category", "values": _BLOB},
            {"name": "comment", "type": "category", "values": _BLOB},
            {"name": "referrer_id", "type": "reference", "field": "id", "null_probability": 0.3},
        ],
    }


# --------------------------------------------------------------------------- #
# Memory sampling                                                             #
# --------------------------------------------------------------------------- #

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def rss_bytes() -> int:
    """Current resident set size of this process, in bytes (Linux /proc)."""
    with open("/proc/self/statm") as fh:
        resident_pages = int(fh.read().split()[1])
    return resident_pages * _PAGE_SIZE


class PeakMemory:
    """Context manager that samples RSS on a background thread and records the peak."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> PeakMemory:
        self.peak = rss_bytes()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.wait(self.interval):
            self.peak = max(self.peak, rss_bytes())

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.peak = max(self.peak, rss_bytes())


# --------------------------------------------------------------------------- #
# Benchmark                                                                   #
# --------------------------------------------------------------------------- #


def benchmark_format(
    fmt: str, template: dict[str, Any], path: Path, batch_size: int
) -> dict[str, Any]:
    engine = SynthEngine(load_and_validate_template(template))
    sink = build_sink(fmt, path)
    start = time.perf_counter()
    with PeakMemory() as mem:
        out = sink.write(engine.iter_rows(), batch_size=batch_size)
    elapsed = time.perf_counter() - start
    return {
        "format": fmt,
        "rows": template["row_count"],
        "seconds": elapsed,
        "rows_per_sec": template["row_count"] / elapsed if elapsed else 0.0,
        "file_bytes": os.path.getsize(out),
        "peak_rss_bytes": mem.peak,
    }


def _human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}PB"


def _print_table(baseline_rss: int, results: list[dict[str, Any]]) -> None:
    header = (
        f"{'format':<8} {'rows':>12} {'time':>9} {'rows/s':>12} "
        f"{'file size':>11} {'peak RSS':>11} {'Δ RSS':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        delta = r["peak_rss_bytes"] - baseline_rss
        print(
            f"{r['format']:<8} {r['rows']:>12,} {r['seconds']:>8.1f}s "
            f"{r['rows_per_sec']:>12,.0f} {_human(r['file_bytes']):>11} "
            f"{_human(r['peak_rss_bytes']):>11} {_human(delta):>10}"
        )
    print(f"\nbaseline RSS (post-import, pre-generation): {_human(baseline_rss)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000_000, help="Rows to generate.")
    parser.add_argument("--batch-size", type=int, default=500, help="Sink write batch size.")
    parser.add_argument(
        "--formats", nargs="+", choices=ALL_FORMATS, default=ALL_FORMATS,
        help="Which sink formats to benchmark.",
    )
    parser.add_argument("--outdir", default=None, help="Where to write output files.")
    parser.add_argument("--keep", action="store_true", help="Keep generated files.")
    args = parser.parse_args(argv)

    outdir = Path(args.outdir) if args.outdir else Path(tempfile.mkdtemp(prefix="synthgen-bench-"))
    outdir.mkdir(parents=True, exist_ok=True)
    template = wide_template(args.rows)

    baseline_rss = rss_bytes()
    print(f"benchmarking {args.rows:,} rows -> {args.formats} in {outdir}\n")

    results = []
    for fmt in args.formats:
        path = outdir / f"bench.{fmt}"
        res = benchmark_format(fmt, template, path, args.batch_size)
        results.append(res)
        print(
            f"  {fmt:<8} done in {res['seconds']:.1f}s  "
            f"({_human(res['file_bytes'])}, peak {_human(res['peak_rss_bytes'])})"
        )

    print()
    _print_table(baseline_rss, results)

    if not args.keep and args.outdir is None:
        shutil.rmtree(outdir, ignore_errors=True)
    else:
        print(f"\nfiles kept in {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

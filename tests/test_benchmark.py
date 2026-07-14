"""Smoke test for the benchmark harness (tiny scale — the real run is manual)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The benchmark lives outside the package; make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

bench = pytest.importorskip("bench_sinks")


def test_wide_template_is_valid() -> None:
    from pysynthgen import load_and_validate_template

    spec = load_and_validate_template(bench.wide_template(10))
    assert spec.row_count == 10
    assert len(spec.fields) >= 12


def test_peak_memory_records_a_value() -> None:
    with bench.PeakMemory(interval=0.001) as mem:
        _ = [bytearray(1024) for _ in range(100)]
    assert mem.peak > 0


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_benchmark_format_runs(tmp_path, fmt) -> None:
    template = bench.wide_template(200)
    result = bench.benchmark_format(fmt, template, tmp_path / f"b.{fmt}", batch_size=50)
    assert result["rows"] == 200
    assert result["file_bytes"] > 0
    assert result["peak_rss_bytes"] > 0
    assert result["rows_per_sec"] > 0


def test_human_readable_sizes() -> None:
    assert bench._human(512) == "512.0B"
    assert bench._human(1536).endswith("KB")
    assert bench._human(5 * 1024**3).endswith("GB")

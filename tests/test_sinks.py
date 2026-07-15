"""Contract tests for the sink implementations."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from pysynthgen import SynthEngine, load_and_validate_template
from pysynthgen.sinks import build_sink, format_from_path


def _rows(null_field: bool = False) -> list[dict]:
    fields = [
        {"name": "id", "type": "uuid"},
        {"name": "seq", "type": "sequence"},
        {
            "name": "created_at",
            "type": "datetime",
            "start": "2024-01-01T00:00:00",
            "end": "2024-12-31T23:59:59",
        },
        {"name": "day", "type": "date", "start": "2024-01-01", "end": "2024-12-31"},
        {"name": "age", "type": "int", "min": 18, "max": 90},
        {"name": "score", "type": "float", "min": 0.0, "max": 1.0},
        {"name": "price", "type": "decimal", "precision": 10, "scale": 2,
         "min": 0.0, "max": 5000.0},
        {"name": "active", "type": "bool", "true_probability": 0.7},
        {"name": "country", "type": "category", "values": ["US", "NL"]},
    ]
    if null_field:
        fields.append({"name": "maybe", "type": "uuid", "null_probability": 1.0})
    spec = load_and_validate_template({"row_count": 250, "seed": 3, "fields": fields})
    return list(SynthEngine(spec).iter_rows())


ALL_FORMATS = ["json", "csv", "parquet", "avro"]


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_roundtrip_row_count(tmp_path, fmt) -> None:
    rows = _rows()
    sink = build_sink(fmt, tmp_path / f"out.{fmt}")
    # Feed in two batches to exercise incremental writing.
    sink.write_batch(rows[:100])
    sink.write_batch(rows[100:])
    path = sink.finalize()
    assert _read_count(fmt, path) == len(rows)


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_null_values_survive(tmp_path, fmt) -> None:
    rows = _rows(null_field=True)
    sink = build_sink(fmt, tmp_path / f"out.{fmt}")
    sink.write_batch(rows)
    path = sink.finalize()
    read = _read_rows(fmt, path)
    # 'maybe' is always null. In csv nulls become empty strings.
    empty = "" if fmt == "csv" else None
    assert all(r["maybe"] == empty for r in read)


def test_json_content_matches(tmp_path) -> None:
    rows = _rows()
    sink = build_sink("json", tmp_path / "out.json")
    sink.write_batch(rows)
    path = sink.finalize()
    data = json.loads(open(path).read())
    assert len(data) == len(rows)
    assert data[0]["id"] == rows[0]["id"]
    # datetime serialized as ISO string
    assert data[0]["created_at"] == rows[0]["created_at"].isoformat()


def test_parquet_preserves_types(tmp_path) -> None:
    pa = pytest.importorskip("pyarrow.parquet")
    rows = _rows()
    sink = build_sink("parquet", tmp_path / "out.parquet")
    sink.write_batch(rows)
    path = sink.finalize()
    table = pa.read_table(path)
    back = table.to_pylist()
    assert isinstance(back[0]["created_at"], datetime)
    assert back[0]["age"] == rows[0]["age"]


def test_avro_datetime_is_utc_and_deterministic(tmp_path) -> None:
    fastavro = pytest.importorskip("fastavro")
    rows = _rows()
    sink = build_sink("avro", tmp_path / "out.avro")
    sink.write_batch(rows)
    path = sink.finalize()
    with open(path, "rb") as fo:
        back = list(fastavro.reader(fo))
    ts = back[0]["created_at"]
    assert ts.tzinfo is not None
    # naive engine datetime, interpreted as UTC, must equal the read-back instant
    assert ts == rows[0]["created_at"].replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("fmt", ["parquet", "avro"])
def test_decimal_roundtrips_exactly(tmp_path, fmt) -> None:
    # The formats with a real decimal type must give back the identical Decimal —
    # exactness is the whole reason to use a decimal field over a float.
    pytest.importorskip({"parquet": "pyarrow", "avro": "fastavro"}[fmt])
    rows = _rows()
    sink = build_sink(fmt, tmp_path / f"out.{fmt}")
    sink.write_batch(rows)
    path = sink.finalize()
    back = _read_rows(fmt, path)
    assert isinstance(back[0]["price"], Decimal)
    assert [r["price"] for r in back] == [r["price"] for r in rows]


def test_parquet_decimal_precision_survives_later_batch(tmp_path) -> None:
    # Regression: arrow infers a decimal's precision from the values it is shown,
    # so a first batch of small values would fix a precision too narrow to hold a
    # larger value arriving in a later batch.
    pytest.importorskip("pyarrow")
    sink = build_sink("parquet", tmp_path / "out.parquet")
    sink.write_batch([{"price": Decimal("1.23")}])
    sink.write_batch([{"price": Decimal("99999999.99")}])
    path = sink.finalize()
    assert [r["price"] for r in _read_rows("parquet", path)] == [
        Decimal("1.23"),
        Decimal("99999999.99"),
    ]


def test_json_decimal_is_an_exact_string(tmp_path) -> None:
    # A JSON number is a float to almost every reader, so decimals go out as
    # strings carrying the exact digits.
    sink = build_sink("json", tmp_path / "out.json")
    sink.write_batch([{"price": Decimal("1.10")}])
    path = sink.finalize()
    assert json.loads(open(path).read()) == [{"price": "1.10"}]


def test_bool_roundtrips_as_bool(tmp_path) -> None:
    pytest.importorskip("pyarrow")
    rows = _rows()
    sink = build_sink("parquet", tmp_path / "out.parquet")
    sink.write_batch(rows)
    path = sink.finalize()
    back = _read_rows("parquet", path)
    assert all(isinstance(r["active"], bool) for r in back)
    assert [r["active"] for r in back] == [r["active"] for r in rows]


def test_csv_custom_delimiter_and_quote(tmp_path) -> None:
    rows = _rows()
    path = tmp_path / "out.csv"
    sink = build_sink("csv", path, delimiter=";", quotechar="'")
    sink.write_batch(rows)
    sink.finalize()
    with open(path, newline="") as fo:
        reader = csv.DictReader(fo, delimiter=";", quotechar="'")
        read = list(reader)
    assert len(read) == len(rows)
    assert read[0]["id"] == rows[0]["id"]


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_write_consumes_iterator(tmp_path, fmt) -> None:
    rows = _rows()
    sink = build_sink(fmt, tmp_path / f"out.{fmt}")
    # pass a generator (not a list) to prove it is consumed lazily
    path = sink.write(iter(rows), batch_size=64)
    assert _read_count(fmt, path) == len(rows)


def test_write_default_batch_size_covers_remainder(tmp_path) -> None:
    # 250 rows with the default 500 batch -> a single trailing batch, still written.
    rows = _rows()
    assert len(rows) < 500
    sink = build_sink("json", tmp_path / "out.json")
    path = sink.write(iter(rows))
    assert _read_count("json", path) == len(rows)


def test_write_empty_iterator_finalizes(tmp_path) -> None:
    sink = build_sink("json", tmp_path / "out.json")
    path = sink.write(iter([]))
    assert json.loads(open(path).read()) == []


def test_write_rejects_bad_batch_size(tmp_path) -> None:
    sink = build_sink("json", tmp_path / "out.json")
    with pytest.raises(ValueError):
        sink.write(iter(_rows()), batch_size=0)


def test_build_sink_unknown_format_raises() -> None:
    with pytest.raises(ValueError):
        build_sink("xml", "out.xml")


def test_format_from_path() -> None:
    assert format_from_path("data.parquet") == "parquet"
    assert format_from_path("data.pq") == "parquet"
    assert format_from_path("data.avro") == "avro"
    with pytest.raises(ValueError):
        format_from_path("data.txt")


def test_empty_write_produces_valid_file(tmp_path) -> None:
    # finalize with no batches must still yield a readable (empty) json array.
    sink = build_sink("json", tmp_path / "empty.json")
    path = sink.finalize()
    assert json.loads(open(path).read()) == []


# -- read-back helpers ---------------------------------------------------- #


def _read_rows(fmt: str, path: str) -> list[dict]:
    if fmt == "json":
        return json.loads(open(path).read())
    if fmt == "csv":
        with open(path, newline="") as fo:
            return list(csv.DictReader(fo))
    if fmt == "parquet":
        import pyarrow.parquet as pq

        return pq.read_table(path).to_pylist()
    if fmt == "avro":
        import fastavro

        with open(path, "rb") as fo:
            return list(fastavro.reader(fo))
    raise AssertionError(fmt)


def _read_count(fmt: str, path: str) -> int:
    return len(_read_rows(fmt, path))

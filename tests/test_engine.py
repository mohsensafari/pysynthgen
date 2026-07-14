"""Behavioural tests for the generation engine."""

from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime

import pytest

from pysynthgen import SynthEngine, load_and_validate_template
from pysynthgen.engine import GenerationError


def _engine(template: dict) -> SynthEngine:
    return SynthEngine(load_and_validate_template(template))


def test_row_count_respected() -> None:
    eng = _engine({"row_count": 37, "seed": 1, "fields": [{"name": "id", "type": "uuid"}]})
    assert sum(1 for _ in eng.iter_rows()) == 37


def test_same_seed_is_reproducible() -> None:
    tpl = {
        "row_count": 50,
        "seed": 7,
        "fields": [
            {"name": "id", "type": "uuid"},
            {"name": "n", "type": "int", "min": 0, "max": 1000},
            {"name": "email", "type": "faker", "provider": "email"},
            {"name": "sku", "type": "regex", "pattern": r"[A-Z]{2}\d{3}"},
        ],
    }
    a = list(_engine(tpl).iter_rows())
    b = list(_engine(tpl).iter_rows())
    assert a == b


def test_different_seed_differs() -> None:
    base = {"row_count": 20, "fields": [{"name": "n", "type": "int", "min": 0, "max": 10_000}]}
    a = list(_engine({**base, "seed": 1}).iter_rows())
    b = list(_engine({**base, "seed": 2}).iter_rows())
    assert a != b


def test_uuid_values_are_valid() -> None:
    eng = _engine({"row_count": 5, "seed": 1, "fields": [{"name": "id", "type": "uuid"}]})
    for row in eng.iter_rows():
        assert uuid.UUID(row["id"]).version == 4


def test_int_uniform_within_bounds() -> None:
    eng = _engine(
        {"row_count": 500, "seed": 1, "fields": [{"name": "n", "type": "int", "min": 5, "max": 9}]}
    )
    values = [r["n"] for r in eng.iter_rows()]
    assert all(5 <= v <= 9 for v in values)
    assert isinstance(values[0], int)


def test_int_normal_distribution_sane() -> None:
    eng = _engine(
        {
            "row_count": 5000,
            "seed": 1,
            "fields": [
                {"name": "age", "type": "int", "distribution": "normal", "mean": 35, "stddev": 10}
            ],
        }
    )
    values = [r["age"] for r in eng.iter_rows()]
    assert 33 < statistics.mean(values) < 37
    assert 8 < statistics.pstdev(values) < 12


def test_int_normal_clamped_to_min() -> None:
    eng = _engine(
        {
            "row_count": 1000,
            "seed": 1,
            "fields": [
                {
                    "name": "age",
                    "type": "int",
                    "distribution": "normal",
                    "mean": 20,
                    "stddev": 10,
                    "min": 18,
                }
            ],
        }
    )
    assert all(r["age"] >= 18 for r in eng.iter_rows())


def test_date_within_range() -> None:
    eng = _engine(
        {
            "row_count": 200,
            "seed": 1,
            "fields": [{"name": "d", "type": "date", "start": "2023-01-01", "end": "2023-01-10"}],
        }
    )
    for row in eng.iter_rows():
        assert isinstance(row["d"], date)
        assert date(2023, 1, 1) <= row["d"] <= date(2023, 1, 10)


def test_datetime_within_range() -> None:
    eng = _engine(
        {
            "row_count": 200,
            "seed": 1,
            "fields": [
                {
                    "name": "ts",
                    "type": "datetime",
                    "start": "2023-01-01T00:00:00",
                    "end": "2023-01-01T12:00:00",
                }
            ],
        }
    )
    lo = datetime(2023, 1, 1, 0, 0, 0)
    hi = datetime(2023, 1, 1, 12, 0, 0)
    for row in eng.iter_rows():
        assert isinstance(row["ts"], datetime)
        assert lo <= row["ts"] <= hi


def test_datetime_varies_within_a_day() -> None:
    # A same-day range must still produce differing times (not just the start).
    eng = _engine(
        {
            "row_count": 50,
            "seed": 1,
            "fields": [
                {
                    "name": "ts",
                    "type": "datetime",
                    "start": "2023-01-01T00:00:00",
                    "end": "2023-01-01T23:59:59",
                }
            ],
        }
    )
    times = {row["ts"] for row in eng.iter_rows()}
    assert len(times) > 1


def test_faker_max_length_truncates() -> None:
    eng = _engine(
        {
            "row_count": 100,
            "seed": 1,
            "fields": [{"name": "e", "type": "faker", "provider": "email", "max_length": 5}],
        }
    )
    assert all(len(r["e"]) <= 5 for r in eng.iter_rows())


def test_regex_max_length_truncates() -> None:
    eng = _engine(
        {
            "row_count": 20,
            "seed": 1,
            "fields": [
                {"name": "s", "type": "regex", "pattern": r"[A-Z]{10}", "max_length": 4}
            ],
        }
    )
    assert all(len(r["s"]) == 4 for r in eng.iter_rows())


def test_category_weights_respected() -> None:
    eng = _engine(
        {
            "row_count": 4000,
            "seed": 1,
            "fields": [
                {
                    "name": "c",
                    "type": "category",
                    "values": ["a", "b"],
                    "weights": [0.9, 0.1],
                }
            ],
        }
    )
    values = [r["c"] for r in eng.iter_rows()]
    assert set(values) <= {"a", "b"}
    share_a = values.count("a") / len(values)
    assert 0.85 < share_a < 0.95


def test_regex_matches_pattern() -> None:
    import re

    eng = _engine(
        {
            "row_count": 20,
            "seed": 1,
            "fields": [{"name": "s", "type": "regex", "pattern": r"[A-Z]{3}-\d{4}"}],
        }
    )
    for row in eng.iter_rows():
        assert re.fullmatch(r"[A-Z]{3}-\d{4}", row["s"])


def test_reference_copies_earlier_field() -> None:
    eng = _engine(
        {
            "row_count": 10,
            "seed": 1,
            "fields": [
                {"name": "id", "type": "uuid"},
                {"name": "ref", "type": "reference", "field": "id"},
            ],
        }
    )
    for row in eng.iter_rows():
        assert row["ref"] == row["id"]


def test_null_probability_one_always_null() -> None:
    eng = _engine(
        {
            "row_count": 20,
            "seed": 1,
            "fields": [{"name": "x", "type": "uuid", "null_probability": 1.0}],
        }
    )
    assert all(r["x"] is None for r in eng.iter_rows())


def test_null_probability_zero_never_null() -> None:
    eng = _engine(
        {
            "row_count": 20,
            "seed": 1,
            "fields": [{"name": "x", "type": "uuid", "null_probability": 0.0}],
        }
    )
    assert all(r["x"] is not None for r in eng.iter_rows())


def test_unique_constraint_holds() -> None:
    eng = _engine(
        {
            "row_count": 1000,
            "seed": 1,
            "fields": [{"name": "id", "type": "uuid"}],
            "constraints": [{"type": "unique", "fields": ["id"]}],
        }
    )
    ids = [r["id"] for r in eng.iter_rows()]
    assert len(ids) == len(set(ids))


def test_unique_exhaustion_raises() -> None:
    # 3 possible values but 10 unique rows requested -> impossible.
    eng = _engine(
        {
            "row_count": 10,
            "seed": 1,
            "fields": [{"name": "c", "type": "category", "values": ["a", "b", "c"]}],
            "constraints": [{"type": "unique", "fields": ["c"]}],
        }
    )
    with pytest.raises(GenerationError):
        list(eng.iter_rows())


def test_iter_batches_chunks_correctly() -> None:
    eng = _engine({"row_count": 25, "seed": 1, "fields": [{"name": "id", "type": "uuid"}]})
    batches = list(eng.iter_batches(batch_size=10))
    assert [len(b) for b in batches] == [10, 10, 5]


def test_iter_batches_rejects_bad_size() -> None:
    eng = _engine({"row_count": 5, "seed": 1, "fields": [{"name": "id", "type": "uuid"}]})
    with pytest.raises(ValueError):
        list(eng.iter_batches(batch_size=0))


def test_no_airflow_import_reachable() -> None:
    # The engine must stay Airflow-agnostic.
    import pysynthgen  # noqa: F401

    assert "airflow" not in sys_modules_names()


def sys_modules_names() -> set[str]:
    import sys

    return {name.split(".")[0] for name in sys.modules}

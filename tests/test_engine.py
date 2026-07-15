"""Behavioural tests for the generation engine."""

from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime
from decimal import Decimal

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


def test_bool_probability_extremes() -> None:
    base = {"row_count": 50, "seed": 1}
    always = _engine({**base, "fields": [{"name": "b", "type": "bool", "true_probability": 1.0}]})
    never = _engine({**base, "fields": [{"name": "b", "type": "bool", "true_probability": 0.0}]})
    assert all(r["b"] is True for r in always.iter_rows())
    assert all(r["b"] is False for r in never.iter_rows())


def test_bool_probability_respected() -> None:
    eng = _engine(
        {
            "row_count": 4000,
            "seed": 1,
            "fields": [{"name": "b", "type": "bool", "true_probability": 0.75}],
        }
    )
    values = [r["b"] for r in eng.iter_rows()]
    assert all(isinstance(v, bool) for v in values)
    assert 0.72 < values.count(True) / len(values) < 0.78


def test_sequence_counts_from_one() -> None:
    eng = _engine({"row_count": 5, "seed": 1, "fields": [{"name": "id", "type": "sequence"}]})
    assert [r["id"] for r in eng.iter_rows()] == [1, 2, 3, 4, 5]


def test_sequence_start_and_step() -> None:
    eng = _engine(
        {
            "row_count": 4,
            "seed": 1,
            "fields": [{"name": "id", "type": "sequence", "start": 100, "step": 5}],
        }
    )
    assert [r["id"] for r in eng.iter_rows()] == [100, 105, 110, 115]


def test_sequence_negative_step() -> None:
    eng = _engine(
        {
            "row_count": 3,
            "seed": 1,
            "fields": [{"name": "id", "type": "sequence", "start": 0, "step": -2}],
        }
    )
    assert [r["id"] for r in eng.iter_rows()] == [0, -2, -4]


def test_sequence_spans_generation_chunks() -> None:
    # More rows than the engine's internal chunk, so the counter must survive
    # across chunks rather than restart at each one.
    from pysynthgen.engine import _GENERATION_CHUNK

    n = _GENERATION_CHUNK * 2 + 7
    eng = _engine({"row_count": n, "seed": 1, "fields": [{"name": "id", "type": "sequence"}]})
    assert [r["id"] for r in eng.iter_rows()] == list(range(1, n + 1))


@pytest.mark.parametrize("batch_size", [1, 7, 500, 1024, 4096])
def test_sequence_identical_across_batch_sizes(batch_size: int) -> None:
    # Output must depend only on the seed, never on how rows are consumed.
    tpl = {
        "row_count": 3000,
        "seed": 1,
        "fields": [{"name": "id", "type": "sequence"}, {"name": "n", "type": "int",
                                                         "min": 0, "max": 100}],
    }
    expected = list(_engine(tpl).iter_rows())
    batched = [row for batch in _engine(tpl).iter_batches(batch_size) for row in batch]
    assert batched == expected


def test_sequence_draws_no_randomness() -> None:
    # A sequence field must not perturb the shared RNG stream, so adding one
    # cannot change any other column.
    base = {"row_count": 100, "seed": 5}
    plain = _engine({**base, "fields": [{"name": "n", "type": "int", "min": 0, "max": 10_000}]})
    with_seq = _engine(
        {
            **base,
            "fields": [
                {"name": "id", "type": "sequence"},
                {"name": "n", "type": "int", "min": 0, "max": 10_000},
            ],
        }
    )
    assert [r["n"] for r in with_seq.iter_rows()] == [r["n"] for r in plain.iter_rows()]


def test_sequence_unaffected_by_unique_retries() -> None:
    # A row regenerated to satisfy a unique constraint keeps the sequence value its
    # position was already given, so the column stays contiguous. The tiny value
    # domain relative to row_count guarantees retries actually happen here.
    eng = _engine(
        {
            "row_count": 200,
            "seed": 1,
            "fields": [
                {"name": "id", "type": "sequence"},
                {"name": "c", "type": "category", "values": [str(i) for i in range(250)]},
            ],
            "constraints": [{"type": "unique", "fields": ["c"]}],
        }
    )
    assert [r["id"] for r in eng.iter_rows()] == list(range(1, 201))


def test_decimal_type_and_scale() -> None:
    eng = _engine(
        {
            "row_count": 200,
            "seed": 1,
            "fields": [
                {"name": "p", "type": "decimal", "precision": 8, "scale": 2,
                 "min": 0.0, "max": 100.0}
            ],
        }
    )
    for row in eng.iter_rows():
        assert isinstance(row["p"], Decimal)
        assert -row["p"].as_tuple().exponent == 2
        assert Decimal("0.00") <= row["p"] <= Decimal("100.00")


def test_decimal_scale_zero_is_integral() -> None:
    eng = _engine(
        {
            "row_count": 50,
            "seed": 1,
            "fields": [
                {"name": "p", "type": "decimal", "precision": 5, "scale": 0,
                 "min": 0.0, "max": 100.0}
            ],
        }
    )
    for row in eng.iter_rows():
        assert row["p"] == row["p"].to_integral_value()


def test_decimal_normal_never_exceeds_precision() -> None:
    # An unbounded normal draw will wander far past precision 4 / scale 2 (±99.99);
    # the generator must clamp so no value can overflow the declared type.
    eng = _engine(
        {
            "row_count": 2000,
            "seed": 1,
            "fields": [
                {
                    "name": "p",
                    "type": "decimal",
                    "precision": 4,
                    "scale": 2,
                    "distribution": "normal",
                    "mean": 0.0,
                    "stddev": 500.0,
                }
            ],
        }
    )
    values = [r["p"] for r in eng.iter_rows()]
    assert all(abs(v) <= Decimal("99.99") for v in values)
    # the clamp must actually be biting, or the test proves nothing
    assert Decimal("99.99") in values


def test_decimal_is_reproducible() -> None:
    tpl = {
        "row_count": 100,
        "seed": 11,
        "fields": [
            {"name": "p", "type": "decimal", "precision": 10, "scale": 3,
             "min": -50.0, "max": 50.0}
        ],
    }
    assert list(_engine(tpl).iter_rows()) == list(_engine(tpl).iter_rows())


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


def test_regex_large_fixed_repeat() -> None:
    # Regression: fixed repeats > rstr's default cap (100) used to crash at generation.
    import re

    eng = _engine(
        {
            "row_count": 10,
            "seed": 1,
            "fields": [{"name": "blob", "type": "regex", "pattern": r"[a-z]{500}"}],
        }
    )
    for row in eng.iter_rows():
        assert len(row["blob"]) == 500
        assert re.fullmatch(r"[a-z]{500}", row["blob"])


def test_regex_large_bounded_repeat_in_range() -> None:
    import re

    eng = _engine(
        {
            "row_count": 50,
            "seed": 2,
            "fields": [{"name": "s", "type": "regex", "pattern": r"[0-9]{200,300}"}],
        }
    )
    for row in eng.iter_rows():
        assert 200 <= len(row["s"]) <= 300
        assert re.fullmatch(r"[0-9]{200,300}", row["s"])


def test_regex_unbounded_quantifier_stays_capped() -> None:
    # A pattern with no large explicit bound keeps the default cap, so `+` cannot
    # explode into an enormous string.
    eng = _engine(
        {
            "row_count": 30,
            "seed": 3,
            "fields": [{"name": "s", "type": "regex", "pattern": r"a+"}],
        }
    )
    assert all(0 < len(row["s"]) <= 100 for row in eng.iter_rows())


def test_regex_large_repeat_is_reproducible() -> None:
    tpl = {
        "row_count": 20,
        "seed": 9,
        "fields": [{"name": "s", "type": "regex", "pattern": r"[a-z]{250}"}],
    }
    a = [r["s"] for r in _engine(tpl).iter_rows()]
    b = [r["s"] for r in _engine(tpl).iter_rows()]
    assert a == b


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


def test_bare_import_does_not_load_optional_backends() -> None:
    # Importing the package must not pull in the optional format backends; they are
    # imported lazily only when their sink is built. Checked in a fresh interpreter
    # so earlier tests that built those sinks cannot affect the result.
    import subprocess
    import sys

    code = (
        "import sys, pysynthgen; "
        "assert 'pyarrow' not in sys.modules, 'pyarrow imported eagerly'; "
        "assert 'fastavro' not in sys.modules, 'fastavro imported eagerly'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)

"""Validation tests for the template schema and loader."""

from __future__ import annotations

import json

import pytest

from pysynthgen import TemplateSpec, load_and_validate_template
from pysynthgen.loader import TemplateError


def _valid_template() -> dict:
    return {
        "row_count": 1000,
        "seed": 42,
        "fields": [
            {"name": "user_id", "type": "uuid"},
            {
                "name": "signup_date",
                "type": "date",
                "start": "2023-01-01",
                "end": "2026-01-01",
            },
            {
                "name": "age",
                "type": "int",
                "distribution": "normal",
                "mean": 35,
                "stddev": 10,
                "min": 18,
            },
            {
                "name": "country",
                "type": "category",
                "values": ["US", "NL", "DE"],
                "weights": [0.5, 0.3, 0.2],
            },
            {"name": "email", "type": "faker", "provider": "email"},
            {"name": "sku", "type": "regex", "pattern": r"[A-Z]{3}-\d{4}"},
            {
                "name": "referrer_id",
                "type": "reference",
                "field": "user_id",
                "null_probability": 0.7,
            },
        ],
        "constraints": [{"type": "unique", "fields": ["user_id"]}],
    }


def test_valid_template_parses() -> None:
    spec = load_and_validate_template(_valid_template())
    assert isinstance(spec, TemplateSpec)
    assert spec.row_count == 1000
    assert len(spec.fields) == 7


def test_null_probability_available_on_every_field() -> None:
    spec = load_and_validate_template(_valid_template())
    # inherited from the shared base -> present on all types, defaulting to 0.
    assert all(hasattr(f, "null_probability") for f in spec.fields)
    assert spec.fields[0].null_probability == 0.0


def test_loads_from_json_string() -> None:
    spec = load_and_validate_template(json.dumps(_valid_template()))
    assert spec.row_count == 1000


def test_loads_from_path(tmp_path) -> None:
    p = tmp_path / "template.json"
    p.write_text(json.dumps(_valid_template()), encoding="utf-8")
    spec = load_and_validate_template(str(p))
    assert spec.row_count == 1000


def test_unknown_field_type_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "x", "type": "does_not_exist"}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_extra_key_forbidden() -> None:
    tpl = _valid_template()
    tpl["fields"][0]["bogus"] = 1
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_category_weights_must_match_values() -> None:
    tpl = _valid_template()
    tpl["fields"] = [
        {"name": "c", "type": "category", "values": ["a", "b"], "weights": [0.5]}
    ]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_category_weights_must_sum_to_one() -> None:
    tpl = _valid_template()
    tpl["fields"] = [
        {"name": "c", "type": "category", "values": ["a", "b"], "weights": [0.5, 0.4]}
    ]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_int_normal_requires_mean_and_stddev() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "n", "type": "int", "distribution": "normal", "mean": 5}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_int_uniform_requires_min_max() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "n", "type": "int", "distribution": "uniform", "min": 0}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_date_start_after_end_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [
        {"name": "d", "type": "date", "start": "2026-01-01", "end": "2023-01-01"}
    ]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_datetime_start_after_end_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [
        {
            "name": "ts",
            "type": "datetime",
            "start": "2026-01-01T00:00:00",
            "end": "2023-01-01T00:00:00",
        }
    ]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_max_length_must_be_positive() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "e", "type": "faker", "provider": "email", "max_length": 0}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_invalid_regex_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "r", "type": "regex", "pattern": "[unclosed"}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_reference_to_later_field_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [
        {"name": "a", "type": "reference", "field": "b"},
        {"name": "b", "type": "uuid"},
    ]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_duplicate_field_names_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "x", "type": "uuid"}, {"name": "x", "type": "uuid"}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_constraint_unknown_field_rejected() -> None:
    tpl = _valid_template()
    tpl["constraints"] = [{"type": "unique", "fields": ["nope"]}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_null_probability_out_of_range_rejected() -> None:
    tpl = _valid_template()
    tpl["fields"] = [{"name": "u", "type": "uuid", "null_probability": 1.5}]
    with pytest.raises(TemplateError):
        load_and_validate_template(tpl)


def test_bad_json_string_raises_template_error() -> None:
    with pytest.raises(TemplateError):
        load_and_validate_template("{not valid json")

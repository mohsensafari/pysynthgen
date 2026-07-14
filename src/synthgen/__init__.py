"""synthgen — template-driven synthetic data generation engine.

Public API. The generation engine is added on top of this validated-template layer.
"""

from __future__ import annotations

from synthgen.engine import GenerationError, SynthEngine
from synthgen.loader import TemplateError, load_and_validate_template
from synthgen.schema import (
    CategoryField,
    ConstraintSpec,
    DateField,
    DatetimeField,
    FakerField,
    FieldSpec,
    FloatField,
    IntField,
    ReferenceField,
    RegexField,
    TemplateSpec,
    UniqueConstraint,
    UUIDField,
)
from synthgen.sinks import BaseSink, build_sink

__all__ = [
    "load_and_validate_template",
    "SynthEngine",
    "GenerationError",
    "BaseSink",
    "build_sink",
    "TemplateError",
    "TemplateSpec",
    "FieldSpec",
    "ConstraintSpec",
    "UUIDField",
    "DateField",
    "DatetimeField",
    "IntField",
    "FloatField",
    "CategoryField",
    "FakerField",
    "RegexField",
    "ReferenceField",
    "UniqueConstraint",
]

__version__ = "0.1.0"

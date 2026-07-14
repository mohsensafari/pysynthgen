"""pysynthgen — template-driven synthetic data generation engine.

Public API. The generation engine is added on top of this validated-template layer.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from pysynthgen.engine import GenerationError, SynthEngine
from pysynthgen.loader import TemplateError, load_and_validate_template
from pysynthgen.schema import (
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
from pysynthgen.sinks import BaseSink, build_sink

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

# Single source of truth is [project].version in pyproject.toml; read it back from
# the installed distribution metadata so there is only one place to bump.
try:
    __version__ = version("pysynthgen")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0.0.0+unknown"

"""Value generators — one per field type — plus the plugin registry.

Each generator turns a single :class:`~pysynthgen.schema._FieldBase` spec into values.
Generators are looked up by their field's ``type`` string via :data:`REGISTRY`,
which is the extension point: register a new type with the :func:`register`
decorator and it becomes usable in templates with no engine changes.

All randomness flows through a shared :class:`RandomBundle` so a single template
seed drives numeric draws, Faker, and regex generation alike.
"""

from __future__ import annotations

import importlib
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from faker import Faker
from rstr import Rstr

from pysynthgen.schema import (
    CategoryField,
    DateField,
    DatetimeField,
    FakerField,
    FloatField,
    IntField,
    ReferenceField,
    RegexField,
    UUIDField,
)

# A row under construction: field name -> already-generated value.
Row = dict[str, Any]


@dataclass
class RandomBundle:
    """The single source of randomness for a generation run."""

    np_rng: np.random.Generator
    faker: Faker
    rstr: Rstr


class BaseGenerator(ABC):
    """Produces the value for one field, given the row built so far."""

    def __init__(self, spec: Any, rng: RandomBundle) -> None:
        self.spec = spec
        self.rng = rng

    @abstractmethod
    def generate(self, row: Row) -> Any:
        """Return this field's value for the current row."""


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #

REGISTRY: dict[str, type[BaseGenerator]] = {}


def register(type_name: str) -> Callable[[type[BaseGenerator]], type[BaseGenerator]]:
    """Register a generator class under a field ``type`` string."""

    def decorator(cls: type[BaseGenerator]) -> type[BaseGenerator]:
        if type_name in REGISTRY:
            raise ValueError(f"generator already registered for type {type_name!r}")
        REGISTRY[type_name] = cls
        return cls

    return decorator


def build_generator(spec: Any, rng: RandomBundle) -> BaseGenerator:
    """Instantiate the generator registered for ``spec.type``."""
    try:
        cls = REGISTRY[spec.type]
    except KeyError:
        raise ValueError(f"no generator registered for field type {spec.type!r}") from None
    return cls(spec, rng)


# --------------------------------------------------------------------------- #
# Generators                                                                  #
# --------------------------------------------------------------------------- #


@register("uuid")
class UUIDGenerator(BaseGenerator):
    spec: UUIDField

    def generate(self, row: Row) -> str:
        # Build a v4-shaped UUID from seeded bytes so runs are reproducible
        # (uuid.uuid4() reads os.urandom and would not be).
        return str(uuid.UUID(bytes=self.rng.np_rng.bytes(16), version=4))


@register("date")
class DateGenerator(BaseGenerator):
    spec: DateField

    def generate(self, row: Row) -> date:
        span = (self.spec.end - self.spec.start).days
        offset = int(self.rng.np_rng.integers(0, span + 1))
        return self.spec.start + timedelta(days=offset)


@register("datetime")
class DatetimeGenerator(BaseGenerator):
    spec: DatetimeField

    def generate(self, row: Row) -> datetime:
        span = int((self.spec.end - self.spec.start).total_seconds())
        offset = int(self.rng.np_rng.integers(0, span + 1))
        return self.spec.start + timedelta(seconds=offset)


@register("int")
class IntGenerator(BaseGenerator):
    spec: IntField

    def generate(self, row: Row) -> int:
        s = self.spec
        if s.distribution == "uniform":
            assert s.min is not None and s.max is not None  # guaranteed by schema
            return int(self.rng.np_rng.integers(s.min, s.max + 1))
        assert s.mean is not None and s.stddev is not None
        value = round(float(self.rng.np_rng.normal(s.mean, s.stddev)))
        if s.min is not None:
            value = max(value, s.min)
        if s.max is not None:
            value = min(value, s.max)
        return value


@register("float")
class FloatGenerator(BaseGenerator):
    spec: FloatField

    def generate(self, row: Row) -> float:
        s = self.spec
        if s.distribution == "uniform":
            assert s.min is not None and s.max is not None
            return float(self.rng.np_rng.uniform(s.min, s.max))
        assert s.mean is not None and s.stddev is not None
        value = float(self.rng.np_rng.normal(s.mean, s.stddev))
        if s.min is not None:
            value = max(value, s.min)
        if s.max is not None:
            value = min(value, s.max)
        return value


@register("category")
class CategoryGenerator(BaseGenerator):
    spec: CategoryField

    def generate(self, row: Row) -> str:
        idx = int(self.rng.np_rng.choice(len(self.spec.values), p=self.spec.weights))
        return self.spec.values[idx]


@register("faker")
class FakerGenerator(BaseGenerator):
    spec: FakerField

    def generate(self, row: Row) -> Any:
        try:
            provider = getattr(self.rng.faker, self.spec.provider)
        except AttributeError:
            raise ValueError(f"unknown faker provider {self.spec.provider!r}") from None
        value = provider()
        if self.spec.max_length is not None and isinstance(value, str):
            value = value[: self.spec.max_length]
        return value


# rstr clamps every repeat to a module-level cap (default 100), which crashes on a
# fixed/bounded repeat larger than that (e.g. `{500}` -> randrange(500, 101)). We raise
# the cap per pattern to cover its explicit bounds only, so unbounded quantifiers
# (`+`, `*`) stay capped at the default and cannot blow up the output.
_xeger_module: Any = importlib.import_module("rstr.xeger")
_DEFAULT_STAR_PLUS_LIMIT: int = int(_xeger_module.STAR_PLUS_LIMIT)

try:  # Python 3.11+
    _sre_parser: Any = importlib.import_module("re._parser")
    _sre_constants: Any = importlib.import_module("re._constants")
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    _sre_parser = importlib.import_module("sre_parse")
    _sre_constants = importlib.import_module("sre_constants")


def _max_explicit_repeat(pattern: str) -> int:
    """Return the largest explicit repeat bound in ``pattern`` (0 if none).

    Unbounded quantifiers report their finite side only (e.g. ``a{5,}`` -> 5), so
    ``+``/``*`` on their own contribute nothing and keep the default cap.
    """
    try:
        parsed = _sre_parser.parse(pattern)
    except re.error:  # pragma: no cover - patterns are pre-validated by the schema
        return 0
    best = 0
    stack: list[Any] = [parsed]
    while stack:
        for op, av in stack.pop():
            if op in (_sre_constants.MAX_REPEAT, _sre_constants.MIN_REPEAT):
                lo, hi, sub = av
                if lo != _sre_constants.MAXREPEAT:
                    best = max(best, lo)
                if hi != _sre_constants.MAXREPEAT:
                    best = max(best, hi)
                stack.append(sub)
            elif op == _sre_constants.SUBPATTERN:
                stack.append(av[-1])
            elif op == _sre_constants.BRANCH:
                stack.extend(av[1])
            elif op in (_sre_constants.ASSERT, _sre_constants.ASSERT_NOT):
                stack.append(av[1])
    return best


@register("regex")
class RegexGenerator(BaseGenerator):
    spec: RegexField

    def __init__(self, spec: Any, rng: RandomBundle) -> None:
        super().__init__(spec, rng)
        self._star_plus_limit = max(_DEFAULT_STAR_PLUS_LIMIT, _max_explicit_repeat(spec.pattern))

    def generate(self, row: Row) -> str:
        _xeger_module.STAR_PLUS_LIMIT = self._star_plus_limit
        value = self.rng.rstr.xeger(self.spec.pattern)
        if self.spec.max_length is not None:
            value = value[: self.spec.max_length]
        return value


@register("reference")
class ReferenceGenerator(BaseGenerator):
    spec: ReferenceField

    def generate(self, row: Row) -> Any:
        # The referenced field is validated to appear earlier, so it is present.
        return row[self.spec.field]

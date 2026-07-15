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
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any

import numpy as np
from faker import Faker
from rstr import Rstr

from pysynthgen.schema import (
    BoolField,
    CategoryField,
    DateField,
    DatetimeField,
    DecimalField,
    FakerField,
    FloatField,
    IntField,
    ReferenceField,
    RegexField,
    SequenceField,
    UUIDField,
)

# A row under construction: field name -> already-generated value.
Row = dict[str, Any]
# A generated column and the map of columns built so far (field name -> values).
Column = list[Any]
Columns = dict[str, Column]


@dataclass
class RandomBundle:
    """The single source of randomness for a generation run."""

    np_rng: np.random.Generator
    faker: Faker
    rstr: Rstr


class BaseGenerator(ABC):
    """Produces the value for one field, given the row built so far."""

    #: True when the value is a function of the row's position rather than of a
    #: random draw. The engine preserves such fields when it regenerates a row to
    #: satisfy a ``unique`` constraint: redrawing a random value is meaningless to
    #: the row, but a positional value belongs to it and must survive the retry.
    positional: bool = False

    def __init__(self, spec: Any, rng: RandomBundle) -> None:
        self.spec = spec
        self.rng = rng

    @abstractmethod
    def generate(self, row: Row) -> Any:
        """Return this field's value for the current row."""

    def generate_column(self, n: int, columns: Columns) -> Column:
        """Return ``n`` values as a column, given the columns built so far.

        The default draws one value per row via :meth:`generate`, each seeing a
        partially-built row assembled from ``columns`` — so a generator that reads
        earlier fields keeps working. Generators whose draw vectorizes (numeric,
        category, uuid, date/time) override this to draw the whole column at once.
        """
        keys = list(columns)
        return [self.generate({k: columns[k][i] for k in keys}) for i in range(n)]


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

    def generate_column(self, n: int, columns: Columns) -> Column:
        raw = self.rng.np_rng.bytes(16 * n)
        return [str(uuid.UUID(bytes=raw[i * 16 : i * 16 + 16], version=4)) for i in range(n)]


@register("bool")
class BoolGenerator(BaseGenerator):
    spec: BoolField

    def generate(self, row: Row) -> bool:
        return bool(self.rng.np_rng.random() < self.spec.true_probability)

    def generate_column(self, n: int, columns: Columns) -> Column:
        draws = self.rng.np_rng.random(n) < self.spec.true_probability
        return [bool(v) for v in draws.tolist()]


@register("sequence")
class SequenceGenerator(BaseGenerator):
    """Counts rows rather than drawing them; see :class:`SequenceField`.

    The counter lives on the generator, which the engine builds once per
    :class:`~pysynthgen.engine.SynthEngine` — so it advances across chunks exactly
    as the shared RNG stream does, and never touches that stream.
    """

    spec: SequenceField
    positional = True

    def __init__(self, spec: Any, rng: RandomBundle) -> None:
        super().__init__(spec, rng)
        self._emitted = 0

    def generate(self, row: Row) -> int:
        value = self.spec.start + self.spec.step * self._emitted
        self._emitted += 1
        return value

    def generate_column(self, n: int, columns: Columns) -> Column:
        start, step = self.spec.start, self.spec.step
        first = self._emitted
        self._emitted += n
        return [start + step * i for i in range(first, first + n)]


@register("date")
class DateGenerator(BaseGenerator):
    spec: DateField

    def generate(self, row: Row) -> date:
        span = (self.spec.end - self.spec.start).days
        offset = int(self.rng.np_rng.integers(0, span + 1))
        return self.spec.start + timedelta(days=offset)

    def generate_column(self, n: int, columns: Columns) -> Column:
        span = (self.spec.end - self.spec.start).days
        start = self.spec.start
        offsets = self.rng.np_rng.integers(0, span + 1, size=n).tolist()
        return [start + timedelta(days=o) for o in offsets]


@register("datetime")
class DatetimeGenerator(BaseGenerator):
    spec: DatetimeField

    def generate(self, row: Row) -> datetime:
        span = int((self.spec.end - self.spec.start).total_seconds())
        offset = int(self.rng.np_rng.integers(0, span + 1))
        return self.spec.start + timedelta(seconds=offset)

    def generate_column(self, n: int, columns: Columns) -> Column:
        span = int((self.spec.end - self.spec.start).total_seconds())
        start = self.spec.start
        offsets = self.rng.np_rng.integers(0, span + 1, size=n).tolist()
        return [start + timedelta(seconds=o) for o in offsets]


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

    def generate_column(self, n: int, columns: Columns) -> Column:
        s = self.spec
        rng = self.rng.np_rng
        if s.distribution == "uniform":
            assert s.min is not None and s.max is not None
            return list(rng.integers(s.min, s.max + 1, size=n).tolist())
        assert s.mean is not None and s.stddev is not None
        arr = np.round(rng.normal(s.mean, s.stddev, size=n))
        if s.min is not None:
            arr = np.maximum(arr, s.min)
        if s.max is not None:
            arr = np.minimum(arr, s.max)
        return [int(v) for v in arr.tolist()]


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

    def generate_column(self, n: int, columns: Columns) -> Column:
        s = self.spec
        rng = self.rng.np_rng
        if s.distribution == "uniform":
            assert s.min is not None and s.max is not None
            return list(rng.uniform(s.min, s.max, size=n).tolist())
        assert s.mean is not None and s.stddev is not None
        arr = rng.normal(s.mean, s.stddev, size=n)
        if s.min is not None:
            arr = np.maximum(arr, s.min)
        if s.max is not None:
            arr = np.minimum(arr, s.max)
        return list(arr.tolist())


@register("decimal")
class DecimalGenerator(BaseGenerator):
    """Draws like ``float``, then snaps the value onto the declared decimal grid.

    Rounding happens once, at the end: the draw is float64, so clamping in float
    space and *then* quantizing could land a value a quantum outside the declared
    bounds. Bounds are therefore held as exact ``Decimal`` and applied after
    quantization, which also guarantees no value can exceed ``precision``.
    """

    spec: DecimalField

    def __init__(self, spec: Any, rng: RandomBundle) -> None:
        super().__init__(spec, rng)
        self._quantum = Decimal(1).scaleb(-spec.scale)
        limit: Decimal = spec.magnitude_limit
        lo = -limit if spec.min is None else max(Decimal(str(spec.min)), -limit)
        hi = limit if spec.max is None else min(Decimal(str(spec.max)), limit)
        # Round the bounds *inward* so a clamped value is always on the grid and
        # still inside the range the template asked for.
        self._lo: Decimal = lo.quantize(self._quantum, rounding=ROUND_CEILING)
        self._hi: Decimal = hi.quantize(self._quantum, rounding=ROUND_FLOOR)

    def _snap(self, value: float) -> Decimal:
        quantized = Decimal(str(value)).quantize(self._quantum, rounding=ROUND_HALF_EVEN)
        return min(max(quantized, self._lo), self._hi)

    def _draw(self, size: int | None) -> Any:
        s = self.spec
        if s.distribution == "uniform":
            assert s.min is not None and s.max is not None  # guaranteed by schema
            return self.rng.np_rng.uniform(s.min, s.max, size=size)
        assert s.mean is not None and s.stddev is not None
        return self.rng.np_rng.normal(s.mean, s.stddev, size=size)

    def generate(self, row: Row) -> Decimal:
        return self._snap(float(self._draw(None)))

    def generate_column(self, n: int, columns: Columns) -> Column:
        return [self._snap(v) for v in self._draw(n).tolist()]


@register("category")
class CategoryGenerator(BaseGenerator):
    spec: CategoryField

    def generate(self, row: Row) -> str:
        idx = int(self.rng.np_rng.choice(len(self.spec.values), p=self.spec.weights))
        return self.spec.values[idx]

    def generate_column(self, n: int, columns: Columns) -> Column:
        values = self.spec.values
        idx = self.rng.np_rng.choice(len(values), size=n, p=self.spec.weights)
        return [values[i] for i in idx.tolist()]


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

    def generate_column(self, n: int, columns: Columns) -> Column:
        # The referenced field is built earlier, so its (null-applied) column is ready.
        return list(columns[self.spec.field])

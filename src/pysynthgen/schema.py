"""Pydantic models describing the synthetic-data template format.

The template is the single source of truth for a generation run. Every field type
is modelled as its own class so validation is *type-specific*: an ``int`` field
cannot accidentally carry a Faker ``provider``, and a ``category`` field must have
weights that line up with its values. The field models are combined into a
discriminated union keyed on the ``type`` string, which is also the key the
generator registry uses to resolve a generator for each field.

Relationships / multi-table are intentionally out of scope for now, but the shape
is designed to grow into them without a rewrite:

* A template describes a single entity (one flat table). A future multi-table
  format can wrap several of these as ``entities: {name: TemplateSpec}`` at a new
  top level without changing the field models.
* ``reference`` currently copies a value from an *earlier field in the same row*.
  A cross-table foreign key later becomes the same type with an optional target
  (e.g. ``entity``/``table``) added — existing intra-row references keep working.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# Widest precision a ``decimal`` field may declare. Values are drawn as float64,
# which carries ~15-17 significant digits, so a wider precision would promise
# exactness the draw cannot deliver. (Parquet's decimal128 would allow 38.)
MAX_DECIMAL_PRECISION = 18

# --------------------------------------------------------------------------- #
# Field specifications                                                        #
# --------------------------------------------------------------------------- #


class _FieldBase(BaseModel):
    """Common attributes shared by every field type.

    ``null_probability`` lives here so any field can be made nullable: on each row
    the generator emits ``None`` with this probability instead of a value.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, description="Column name in the output row.")
    null_probability: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Chance of emitting None for this field."
    )


class _StringFieldBase(_FieldBase):
    """Base for fields that emit strings; supports an optional length cap.

    ``max_length`` truncates the generated value to at most that many characters.
    """

    max_length: int | None = Field(
        default=None, ge=1, description="Truncate the generated string to this many characters."
    )


class UUIDField(_FieldBase):
    type: Literal["uuid"] = "uuid"


class BoolField(_FieldBase):
    type: Literal["bool"] = "bool"
    true_probability: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Chance of emitting True."
    )


class SequenceField(_FieldBase):
    """A monotonic counter, the usual stand-in for an auto-increment primary key.

    Unlike every other field this draws no randomness, so adding one cannot change
    any other column: its value is a function of the row's position alone. The
    column is therefore always exactly ``start``, ``start + step``, ... for
    ``row_count`` rows — a row regenerated to satisfy a ``unique`` constraint keeps
    the value its position was already given, leaving no gap.
    """

    type: Literal["sequence"] = "sequence"
    start: int = Field(default=1, description="Value of the first row.")
    step: int = Field(default=1, description="Added per row; may be negative.")

    @model_validator(mode="after")
    def _check_step(self) -> SequenceField:
        if self.step == 0:
            raise ValueError(f"sequence field {self.name!r}: step must not be 0")
        return self


class DateField(_FieldBase):
    type: Literal["date"] = "date"
    start: date
    end: date

    @model_validator(mode="after")
    def _check_range(self) -> DateField:
        if self.start > self.end:
            raise ValueError(
                f"date field {self.name!r}: start {self.start} is after end {self.end}"
            )
        return self


class DatetimeField(_FieldBase):
    type: Literal["datetime"] = "datetime"
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _check_range(self) -> DatetimeField:
        if self.start > self.end:
            raise ValueError(
                f"datetime field {self.name!r}: start {self.start} is after end {self.end}"
            )
        return self


class IntField(_FieldBase):
    type: Literal["int"] = "int"
    distribution: Literal["uniform", "normal"] = "uniform"
    # uniform params
    min: int | None = None
    max: int | None = None
    # normal params
    mean: float | None = None
    stddev: float | None = None

    @model_validator(mode="after")
    def _check_params(self) -> IntField:
        if self.distribution == "uniform":
            if self.min is None or self.max is None:
                raise ValueError(f"int field {self.name!r}: uniform requires 'min' and 'max'")
            if self.min > self.max:
                raise ValueError(f"int field {self.name!r}: min {self.min} > max {self.max}")
        else:  # normal
            if self.mean is None or self.stddev is None:
                raise ValueError(f"int field {self.name!r}: normal requires 'mean' and 'stddev'")
            if self.stddev <= 0:
                raise ValueError(f"int field {self.name!r}: stddev must be > 0")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(f"int field {self.name!r}: clamp min {self.min} > max {self.max}")
        return self


class FloatField(_FieldBase):
    type: Literal["float"] = "float"
    distribution: Literal["uniform", "normal"] = "uniform"
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    stddev: float | None = None

    @model_validator(mode="after")
    def _check_params(self) -> FloatField:
        if self.distribution == "uniform":
            if self.min is None or self.max is None:
                raise ValueError(f"float field {self.name!r}: uniform requires 'min' and 'max'")
            if self.min > self.max:
                raise ValueError(f"float field {self.name!r}: min {self.min} > max {self.max}")
        else:  # normal
            if self.mean is None or self.stddev is None:
                raise ValueError(f"float field {self.name!r}: normal requires 'mean' and 'stddev'")
            if self.stddev <= 0:
                raise ValueError(f"float field {self.name!r}: stddev must be > 0")
        return self


class DecimalField(_FieldBase):
    """Emits ``decimal.Decimal`` values with a fixed ``precision`` and ``scale``.

    Use this rather than ``float`` for money: a float cannot represent most decimal
    fractions exactly, and both Parquet and Avro carry a proper decimal type that a
    float field cannot reach.

    ``precision`` (total significant digits) doubles as a hard bound: every value is
    clamped to what those digits can hold, so a drawn value can never overflow the
    declared type. ``distribution`` and its parameters mirror ``float``.
    """

    type: Literal["decimal"] = "decimal"
    precision: int = Field(..., ge=1, le=MAX_DECIMAL_PRECISION)
    scale: int = Field(..., ge=0)
    distribution: Literal["uniform", "normal"] = "uniform"
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    stddev: float | None = None

    @property
    def magnitude_limit(self) -> Decimal:
        """The largest magnitude ``precision``/``scale`` can represent."""
        return Decimal(10) ** (self.precision - self.scale) - Decimal(1).scaleb(-self.scale)

    @model_validator(mode="after")
    def _check_params(self) -> DecimalField:
        if self.scale > self.precision:
            raise ValueError(
                f"decimal field {self.name!r}: scale {self.scale} > precision {self.precision}"
            )
        if self.distribution == "uniform":
            if self.min is None or self.max is None:
                raise ValueError(f"decimal field {self.name!r}: uniform requires 'min' and 'max'")
            if self.min > self.max:
                raise ValueError(f"decimal field {self.name!r}: min {self.min} > max {self.max}")
        else:  # normal
            if self.mean is None or self.stddev is None:
                raise ValueError(
                    f"decimal field {self.name!r}: normal requires 'mean' and 'stddev'"
                )
            if self.stddev <= 0:
                raise ValueError(f"decimal field {self.name!r}: stddev must be > 0")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError(
                    f"decimal field {self.name!r}: clamp min {self.min} > max {self.max}"
                )

        # Bounds outside the declared digits would be silently clamped away; reject
        # them instead of generating a column that ignores what the template asked for.
        limit = self.magnitude_limit
        for bound in ("min", "max"):
            value = getattr(self, bound)
            if value is not None and abs(Decimal(str(value))) > limit:
                raise ValueError(
                    f"decimal field {self.name!r}: {bound} {value} exceeds what "
                    f"precision {self.precision}/scale {self.scale} can hold (±{limit})"
                )
        return self


class CategoryField(_FieldBase):
    type: Literal["category"] = "category"
    values: list[str] = Field(..., min_length=1)
    weights: list[float] | None = None

    @model_validator(mode="after")
    def _check_weights(self) -> CategoryField:
        if self.weights is not None:
            if len(self.weights) != len(self.values):
                raise ValueError(
                    f"category field {self.name!r}: {len(self.weights)} weights "
                    f"for {len(self.values)} values"
                )
            if any(w < 0 for w in self.weights):
                raise ValueError(f"category field {self.name!r}: weights must be non-negative")
            total = sum(self.weights)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(
                    f"category field {self.name!r}: weights sum to {total}, expected 1.0"
                )
        return self


class FakerField(_StringFieldBase):
    type: Literal["faker"] = "faker"
    provider: str = Field(..., min_length=1, description="Faker provider method, e.g. 'email'.")


class RegexField(_StringFieldBase):
    """Generates a string matching ``pattern`` (regex reversed into a value).

    A ``max_length`` cap is applied after generation and may break the pattern
    match; use it only when a hard length ceiling matters more than an exact match.
    """

    type: Literal["regex"] = "regex"
    pattern: str = Field(..., min_length=1, description="Regular expression to satisfy.")

    @model_validator(mode="after")
    def _check_pattern(self) -> RegexField:
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"regex field {self.name!r}: invalid pattern: {exc}") from exc
        return self


class ReferenceField(_FieldBase):
    """Copies the value of an earlier field in the same row (intra-row FK).

    A cross-table foreign key will later add an optional target field here; the
    intra-row default keeps working unchanged.
    """

    type: Literal["reference"] = "reference"
    field: str = Field(..., min_length=1, description="Name of an earlier field to reference.")


FieldSpec = Annotated[
    UUIDField
    | BoolField
    | SequenceField
    | DateField
    | DatetimeField
    | IntField
    | FloatField
    | DecimalField
    | CategoryField
    | FakerField
    | RegexField
    | ReferenceField,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- #
# Constraints                                                                 #
# --------------------------------------------------------------------------- #


class UniqueConstraint(BaseModel):
    model_config = {"extra": "forbid"}

    type: Literal["unique"] = "unique"
    fields: list[str] = Field(..., min_length=1)


# Only one constraint type today. When a second is added this becomes a
# discriminated union on "type", mirroring FieldSpec.
ConstraintSpec = UniqueConstraint


# --------------------------------------------------------------------------- #
# Top-level template (single entity / flat table)                            #
# --------------------------------------------------------------------------- #


class TemplateSpec(BaseModel):
    """A fully validated generation template for one flat table."""

    model_config = {"extra": "forbid"}

    row_count: int = Field(..., gt=0)
    seed: int | None = None
    fields: list[FieldSpec] = Field(..., min_length=1)
    constraints: list[ConstraintSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cross_field(self) -> TemplateSpec:
        names = [f.name for f in self.fields]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate field names: {sorted(dupes)}")

        # references must point at a field defined *earlier* in the list.
        seen: set[str] = set()
        for f in self.fields:
            if isinstance(f, ReferenceField) and f.field not in seen:
                raise ValueError(
                    f"reference field {f.name!r} points to {f.field!r}, which is not "
                    "defined earlier in 'fields'"
                )
            seen.add(f.name)

        known = set(names)
        for c in self.constraints:
            unknown = [fld for fld in c.fields if fld not in known]
            if unknown:
                raise ValueError(f"{c.type} constraint references unknown fields: {unknown}")
        return self

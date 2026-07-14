"""The generation engine: turns a validated template into rows of synthetic data."""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Any

import numpy as np
from faker import Faker
from rstr import Rstr

from synthgen.generators import BaseGenerator, RandomBundle, Row, build_generator
from synthgen.schema import TemplateSpec


class GenerationError(RuntimeError):
    """Raised when the engine cannot produce a valid row (e.g. uniqueness exhausted)."""


# How many times to regenerate a row that violates a unique constraint before
# giving up. Collisions are astronomically unlikely for UUIDs but easy to hit for
# a unique field drawn from a tiny domain.
_MAX_UNIQUE_RETRIES = 100


class SynthEngine:
    """Generates rows from a :class:`TemplateSpec`.

    A single ``seed`` drives every source of randomness, so two engines built from
    the same spec yield identical output. Consume rows one at a time with
    :meth:`iter_rows`, or in chunks with :meth:`iter_batches`.
    """

    def __init__(self, spec: TemplateSpec) -> None:
        self.spec = spec
        self._rng = _build_bundle(spec.seed)
        self._generators: list[tuple[str, float, BaseGenerator]] = [
            (f.name, f.null_probability, build_generator(f, self._rng)) for f in spec.fields
        ]
        # One seen-set per unique constraint; keys are tuples of the constraint's
        # field values so multi-column unique keys work.
        self._unique: list[tuple[list[str], set[tuple[Any, ...]]]] = [
            (c.fields, set()) for c in spec.constraints if c.type == "unique"
        ]

    def iter_rows(self) -> Iterator[Row]:
        """Yield exactly ``row_count`` generated rows."""
        for _ in range(self.spec.row_count):
            yield self._make_row()

    def iter_batches(self, batch_size: int) -> Iterator[list[Row]]:
        """Yield rows in lists of up to ``batch_size`` (last batch may be smaller)."""
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        batch: list[Row] = []
        for row in self.iter_rows():
            batch.append(row)
            if len(batch) == batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    # -- internals -------------------------------------------------------- #

    def _make_row(self) -> Row:
        for _ in range(_MAX_UNIQUE_RETRIES + 1):
            row = self._generate_once()
            if self._unique_ok(row):
                self._record_unique(row)
                return row
        raise GenerationError(
            f"could not satisfy unique constraint(s) after {_MAX_UNIQUE_RETRIES} retries; "
            "the value domain is likely too small for row_count"
        )

    def _generate_once(self) -> Row:
        row: Row = {}
        for name, null_p, gen in self._generators:
            if null_p > 0.0 and self._rng.np_rng.random() < null_p:
                row[name] = None
            else:
                row[name] = gen.generate(row)
        return row

    def _unique_ok(self, row: Row) -> bool:
        return all(
            tuple(row[f] for f in fields) not in seen for fields, seen in self._unique
        )

    def _record_unique(self, row: Row) -> None:
        for fields, seen in self._unique:
            seen.add(tuple(row[f] for f in fields))


def _build_bundle(seed: int | None) -> RandomBundle:
    faker = Faker()
    if seed is not None:
        faker.seed_instance(seed)
    return RandomBundle(
        np_rng=np.random.default_rng(seed),
        faker=faker,
        rstr=Rstr(random.Random(seed)),
    )

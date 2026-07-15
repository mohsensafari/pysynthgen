"""The generation engine: turns a validated template into rows of synthetic data."""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Any

import numpy as np
from faker import Faker
from rstr import Rstr

from pysynthgen.generators import BaseGenerator, Columns, RandomBundle, Row, build_generator
from pysynthgen.schema import TemplateSpec


class GenerationError(RuntimeError):
    """Raised when the engine cannot produce a valid row (e.g. uniqueness exhausted)."""


# How many times to regenerate a row that violates a unique constraint before
# giving up. Collisions are astronomically unlikely for UUIDs but easy to hit for
# a unique field drawn from a tiny domain.
_MAX_UNIQUE_RETRIES = 100

# Rows generated per internal pass. Each field's column is drawn in one vectorized
# call per chunk, so a larger chunk amortizes numpy's per-call overhead better; a
# bounded chunk keeps peak memory flat regardless of ``row_count``. The value is
# fixed (not the caller's batch size) so output depends only on the seed, never on
# how rows are consumed.
_GENERATION_CHUNK = 1024


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
        remaining = self.spec.row_count
        while remaining > 0:
            chunk = min(_GENERATION_CHUNK, remaining)
            yield from self._generate_chunk(chunk)
            remaining -= chunk

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

    def _generate_chunk(self, n: int) -> list[Row]:
        """Build ``n`` rows a column at a time, then transpose into row dicts."""
        columns: Columns = {}
        for name, null_p, gen in self._generators:
            col = gen.generate_column(n, columns)
            if null_p > 0.0:
                mask = self._rng.np_rng.random(n) < null_p
                col = [None if m else v for m, v in zip(mask, col, strict=True)]
            columns[name] = col

        names = list(columns)
        rows = [
            dict(zip(names, values, strict=True))
            for values in zip(*columns.values(), strict=True)
        ]
        if self._unique:
            rows = [self._make_unique(row) for row in rows]
        return rows

    def _make_unique(self, row: Row) -> Row:
        """Keep ``row`` if it satisfies every unique constraint, else regenerate it."""
        for _ in range(_MAX_UNIQUE_RETRIES + 1):
            if self._unique_ok(row):
                self._record_unique(row)
                return row
            row = self._generate_once()
        raise GenerationError(
            f"could not satisfy unique constraint(s) after {_MAX_UNIQUE_RETRIES} retries; "
            "the value domain is likely too small for row_count"
        )

    def _generate_once(self) -> Row:
        """Draw a single row one field at a time (the per-row uniqueness fallback)."""
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

"""Generator throughput: per-row draws vs column-at-a-time (vectorized) draws.

The engine currently draws one value per cell — one numpy call per field per row.
numpy's per-call overhead dwarfs the arithmetic, so for the numpy/uuid-bound field
types the win is calling into numpy *once per column per batch* instead of once per
cell. This benchmark quantifies that headroom and asserts the batched draw is
value-identical to the scalar draw for the same seed (per column, in isolation).

    python benchmarks/bench_generators.py --rows 200000

Reading the results: `int`/`float`/`category` collapse to near-free because their
residual cost was pure boundary overhead; `uuid`/`datetime` gain less because they
are bounded by inherent per-row Python object construction (uuid.UUID, datetime)
that batching cannot remove. That gap is exactly why a native/C core would not pay
off here — the leftover cost is object materialization, not compute.

This measures the *generator* draw in isolation. It does not model the engine's
cross-column RNG interleaving: moving the engine to column-at-a-time draws changes
the global draw order and therefore seeded output — a deliberate one-time break to
be handled where the engine change lands, not here.
"""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np

START = datetime(2020, 1, 1)
END = datetime(2026, 1, 1)
SPAN = int((END - START).total_seconds())
VALUES = ["US", "NL", "DE", "FR", "GB", "ES", "IT", "SE"]
WEIGHTS = [0.30, 0.20, 0.15, 0.10, 0.08, 0.07, 0.06, 0.04]

Column = Callable[[np.random.Generator, int], list[Any]]


# --------------------------------------------------------------------------- #
# Per-row generators (mirror the current engine's one-draw-per-cell path)     #
# --------------------------------------------------------------------------- #


def perrow_int(rng: np.random.Generator, n: int) -> list[Any]:
    return [int(rng.integers(18, 96)) for _ in range(n)]


def perrow_float(rng: np.random.Generator, n: int) -> list[Any]:
    out: list[Any] = []
    for _ in range(n):
        v = float(rng.normal(500.0, 250.0))
        out.append(max(0.0, v))
    return out


def perrow_category(rng: np.random.Generator, n: int) -> list[Any]:
    return [VALUES[int(rng.choice(len(VALUES), p=WEIGHTS))] for _ in range(n)]


def perrow_uuid(rng: np.random.Generator, n: int) -> list[Any]:
    return [str(uuid.UUID(bytes=rng.bytes(16), version=4)) for _ in range(n)]


def perrow_datetime(rng: np.random.Generator, n: int) -> list[Any]:
    return [START + timedelta(seconds=int(rng.integers(0, SPAN + 1))) for _ in range(n)]


# --------------------------------------------------------------------------- #
# Vectorized generators (one draw per column)                                 #
# --------------------------------------------------------------------------- #


def vec_int(rng: np.random.Generator, n: int) -> list[Any]:
    return list(rng.integers(18, 96, size=n).tolist())


def vec_float(rng: np.random.Generator, n: int) -> list[Any]:
    a = rng.normal(500.0, 250.0, size=n)
    np.clip(a, 0.0, None, out=a)
    return list(a.tolist())


def vec_category(rng: np.random.Generator, n: int) -> list[Any]:
    idx = rng.choice(len(VALUES), size=n, p=WEIGHTS)
    return list(np.array(VALUES, dtype=object)[idx].tolist())


def vec_uuid(rng: np.random.Generator, n: int) -> list[Any]:
    raw = rng.bytes(16 * n)
    return [str(uuid.UUID(bytes=raw[i * 16 : i * 16 + 16], version=4)) for i in range(n)]


def vec_datetime(rng: np.random.Generator, n: int) -> list[Any]:
    offsets = rng.integers(0, SPAN + 1, size=n).tolist()
    return [START + timedelta(seconds=o) for o in offsets]


CASES: list[tuple[str, Column, Column]] = [
    ("int (uniform)", perrow_int, vec_int),
    ("float (normal+clamp)", perrow_float, vec_float),
    ("category (weighted)", perrow_category, vec_category),
    ("uuid", perrow_uuid, vec_uuid),
    ("datetime", perrow_datetime, vec_datetime),
]


# --------------------------------------------------------------------------- #
# Benchmark                                                                   #
# --------------------------------------------------------------------------- #


def _time(fn: Column, n: int, seed: int) -> tuple[float, list[Any]]:
    fn(np.random.default_rng(seed), min(n, 1000))  # warm
    start = time.perf_counter()
    out = fn(np.random.default_rng(seed), n)
    return time.perf_counter() - start, out


def _print_table(results: list[dict[str, Any]]) -> None:
    header = (
        f"{'field':<22} {'per-row':>12} {'vectorized':>12} {'speedup':>9} {'identical':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['field']:<22} {r['perrow_mps']:>10.2f} M/s {r['vec_mps']:>10.2f} M/s "
            f"{r['speedup']:>8.1f}x {str(r['identical']):>10}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000, help="Values per column.")
    parser.add_argument("--seed", type=int, default=0, help="Seed for both paths.")
    args = parser.parse_args(argv)

    print(f"benchmarking {args.rows:,} values per column (seed {args.seed})\n")

    results: list[dict[str, Any]] = []
    for name, perrow, vec in CASES:
        t_pr, out_pr = _time(perrow, args.rows, args.seed)
        t_vec, out_vec = _time(vec, args.rows, args.seed)
        results.append(
            {
                "field": name,
                "perrow_mps": args.rows / t_pr / 1e6 if t_pr else 0.0,
                "vec_mps": args.rows / t_vec / 1e6 if t_vec else 0.0,
                "speedup": t_pr / t_vec if t_vec else 0.0,
                "identical": out_pr == out_vec,
            }
        )

    _print_table(results)

    if not all(r["identical"] for r in results):
        print("\nWARNING: a vectorized column diverged from its per-row draw")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

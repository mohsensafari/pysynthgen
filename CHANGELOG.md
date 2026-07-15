# CHANGELOG


## v0.2.0 (2026-07-15)

### Features

- Add bool, sequence, and decimal field types
  ([#15](https://github.com/mohsensafari/pysynthgen/pull/15),
  [`0c0bf7b`](https://github.com/mohsensafari/pysynthgen/commit/0c0bf7b9fa7f3f5574d6c0a9dff179ab82ec2c7f))

Adds three field types that had no expression in the template format:

- bool, with an optional true_probability - sequence, a monotonic auto-increment counter - decimal,
  exact fixed precision/scale values for money

sequence is the first generator whose value comes from the row's position rather than a draw. That
  earns BaseGenerator a `positional` flag: the engine carries such fields over when it regenerates a
  row for a unique constraint, since redrawing a positional value only shuffles it out of order.
  Sequences therefore stay contiguous, and draw no randomness at all, so adding one cannot change
  any other column.

decimal caps precision at 18 rather than decimal128's 38 because the draw is float64-backed. Bounds
  are held as exact Decimal and applied after quantization, so a value can never land outside the
  declared bounds or overflow the declared precision.

The parquet sink now pins decimal columns to the maximum precision. Arrow infers precision from the
  values it is shown, so the first batch would otherwise fix a precision too narrow for a larger
  value arriving in a later batch. The avro sink maps Decimal to a bytes/decimal logical type, and
  json emits the exact digits as a string rather than lose them to a float.

The template skill's profiler learns to infer all three from a sample; a stored Decimal previously
  degraded to float, and booleans to a string category. Both skills document the new types.

Closes #6


## v0.1.3 (2026-07-15)

### Documentation

- Add Claude skill for generating pysynthgen templates
  ([#2](https://github.com/mohsensafari/pysynthgen/pull/2),
  [`64001f1`](https://github.com/mohsensafari/pysynthgen/commit/64001f115586190473f96db2108020de9a0f169d))

- Add developer skill with architecture, conventions, and release policy
  ([#3](https://github.com/mohsensafari/pysynthgen/pull/3),
  [`aa7ec5a`](https://github.com/mohsensafari/pysynthgen/commit/aa7ec5a347019eb6ec12fdf8306851cb834774c0))

### Performance Improvements

- Generate rows column-at-a-time instead of cell-by-cell
  ([#4](https://github.com/mohsensafari/pysynthgen/pull/4),
  [`87092d2`](https://github.com/mohsensafari/pysynthgen/commit/87092d2285b74d553a5bc97dff1805b18ad1577a))

* chore: add per-row vs vectorized generator benchmark

Quantifies the headroom in moving the engine from one-draw-per-cell to column-at-a-time (vectorized)
  numpy draws, and asserts the batched draw is value-identical to the scalar draw per column.
  Evidence for a follow-up engine change; no runtime code is touched.

* perf: generate rows column-at-a-time instead of cell-by-cell

The engine drew one value per cell — one numpy call per field per row — so numpy's per-call overhead
  dominated a barely-there arithmetic cost. Generators now expose generate_column(), and the engine
  builds each field's whole column in one vectorized draw per fixed-size chunk, then transposes into
  row dicts. The five numpy/uuid-bound types (int, float, category, uuid, date/datetime) and
  reference vectorize; faker/regex and any custom generator keep a per-row fallback that still sees
  the partially-built row.

~10x more rows/s on a mixed template (23k -> 245k/s). Peak memory stays flat: the chunk size is
  fixed, independent of the caller's batch size, so output depends only on the seed and never on how
  rows are consumed (iter_rows and iter_batches agree for any batch size). Uniqueness still
  regenerates colliding rows per-row.

The column-major draw order changes which values a given seed produces. Same-spec reproducibility is
  unchanged; exact per-seed values differ from prior versions.

* docs: add install/extras section and document column generation

README gains an Installation section: pip install pysynthgen for the engine plus the stdlib json/csv
  sinks, and the parquet/avro/all extras for the heavy backends, with a note that only what you
  install gets imported. Verified against a clean venv, including the error a missing extra raises.

Also brings the docs in line with column-at-a-time generation: the Design section now covers the
  vectorized draw, the fixed generation chunk, and that output no longer depends on how rows are
  consumed; the dev skill documents generate_column, the chunk-size invariant, and where nullability
  now sits in the draw order.

mypy in CI covered only bench_sinks.py, so the new benchmark went unchecked — widened to
  benchmarks/, and README/skill now quote the command CI actually runs.


## v0.1.2 (2026-07-14)

### Bug Fixes

- Support regex patterns with fixed repeats over 100
  ([`36ae28c`](https://github.com/mohsensafari/pysynthgen/commit/36ae28cb5ead60c7a136cfb4e24b18ec711a1e0b))

### Continuous Integration

- Add branching policy and automated semantic-release pipeline
  ([`66629d2`](https://github.com/mohsensafari/pysynthgen/commit/66629d2ead80d89f82ec503356b5b69c751ae1a5))

- Install into uv venv instead of externally-managed system Python
  ([`e4e35c6`](https://github.com/mohsensafari/pysynthgen/commit/e4e35c6877a6a403141887cef0dd90ef3e66a7ed))

- Skip mypy following numpy stubs (PEP 695 target mismatch)
  ([`d024b26`](https://github.com/mohsensafari/pysynthgen/commit/d024b26b3551317c152dfb99363148ceab6d6866))

- Use uv-managed venv from setup-uv instead of creating a new one
  ([`66434f5`](https://github.com/mohsensafari/pysynthgen/commit/66434f5a1a63049da0a84e2d195c2b99c04ab1dc))


## v0.1.1 (2026-07-14)

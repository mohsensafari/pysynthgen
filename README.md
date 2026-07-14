# synthgen

Template-driven synthetic data generation engine.

`synthgen` takes a validated JSON template describing a dataset and yields synthetic
rows as an `Iterator[dict]`. It is **fully decoupled from Airflow** — a separate
Airflow 3 operator (built later) is a thin wrapper around this engine.

## Design

- **Engine is standalone.** The `synthgen` package has zero Airflow imports. It
  takes a validated template and returns an iterator of rows. Airflow integration
  lives outside this package.
- **Templates are validated with Pydantic**, not raw dict parsing. Each field type
  is its own model in a discriminated union keyed on `type`, so validation is
  type-specific and mistakes are caught up front.
- **Generation is seeded and reproducible.** A single `seed` at the template level
  drives all randomness (numeric, Faker, regex), so a run is byte-for-byte
  repeatable.
- **Streaming = chunked generation.** The engine exposes `iter_rows()` and
  `iter_batches(batch_size)`; the consumer (a sink, a file writer, the future
  operator) decides how to persist batches.
- **Relationships are out of scope for now** but the schema is shaped to grow into
  multi-table generation without a rewrite (a future `entities: {name: template}`
  wrapper, and cross-table `reference` fields).

## Template format

```json
{
  "row_count": 100000,
  "seed": 42,
  "fields": [
    {"name": "user_id", "type": "uuid"},
    {"name": "signup_date", "type": "date", "start": "2023-01-01", "end": "2026-01-01"},
    {"name": "age", "type": "int", "distribution": "normal", "mean": 35, "stddev": 10, "min": 18},
    {"name": "country", "type": "category", "values": ["US", "NL", "DE"], "weights": [0.5, 0.3, 0.2]},
    {"name": "email", "type": "faker", "provider": "email"},
    {"name": "sku", "type": "regex", "pattern": "[A-Z]{3}-\\d{4}"},
    {"name": "referrer_id", "type": "reference", "field": "user_id", "null_probability": 0.7}
  ],
  "constraints": [
    {"type": "unique", "fields": ["user_id"]}
  ]
}
```

### Field types

| `type` | Produces | Key params |
|--------|----------|------------|
| `uuid` | UUIDv4 string | — |
| `date` | date between bounds | `start`, `end` |
| `datetime` | datetime between bounds | `start`, `end` |
| `int` | integer | `distribution` (`uniform`/`normal`), `min`/`max` or `mean`/`stddev` |
| `float` | float | same as `int` |
| `category` | value from a set | `values`, optional `weights` (sum to 1) |
| `faker` | Faker output | `provider` (e.g. `email`), optional `max_length` |
| `regex` | string matching a pattern | `pattern`, optional `max_length` |
| `reference` | copy of an earlier field in the same row | `field` |

String-producing fields (`faker`, `regex`) accept an optional `max_length` that
truncates the generated value. For `regex`, truncation may break the pattern match.

Every field also accepts `null_probability` (0–1): the chance of emitting `null`
for that field on a given row.

### Constraints

- `unique` — the listed field(s) form a unique key across generated rows.

## Usage

```python
from synthgen import load_and_validate_template, SynthEngine, build_sink

spec = load_and_validate_template("template.json")
engine = SynthEngine(spec)

# iterate rows directly
for row in engine.iter_rows():
    ...

# or write the whole dataset to a sink in one call (batches internally)
sink = build_sink("parquet", "out.parquet")
path = sink.write(engine.iter_rows())  # -> "out.parquet"

# for finer control, drive the batches yourself
sink = build_sink("parquet", "out.parquet")
for batch in engine.iter_batches(batch_size=10_000):
    sink.write_batch(batch)
path = sink.finalize()
```

Command line:

```bash
python -m synthgen template.json                     # validate + echo the normalized spec
python -m synthgen template.json --rows 20           # print 20 sample rows as JSON
python -m synthgen template.json --out data.parquet  # generate full dataset to a file
python -m synthgen template.json --out data --format avro
```

## Sinks

A sink consumes rows in batches and writes one output artifact. All sinks
implement `write_batch(rows)` / `finalize() -> path`, and `build_sink(format, path)`
selects one by name.

| format | extension | dependency |
|--------|-----------|------------|
| `json` | `.json` | stdlib (streamed JSON array) |
| `csv` | `.csv` | stdlib (`delimiter`/`quotechar` configurable) |
| `parquet` | `.parquet`, `.pq` | `pyarrow` — `synthgen[parquet]` |
| `avro` | `.avro` | `fastavro` — `synthgen[avro]` |

Notes: parquet/avro infer their schema from the first batch with all columns
nullable; avro writes naive datetimes as UTC so output is deterministic across
machines. Install both format deps with `synthgen[all]`.

## Benchmarks

`benchmarks/bench_sinks.py` streams a wide dataset into each sink format and reports
throughput, output size, and **peak resident memory sampled while writing**. Because
the engine yields rows lazily and sinks write in batches, memory stays flat as row
count grows — a multi-GB file is written with tens of MB of RSS.

```bash
python benchmarks/bench_sinks.py --rows 200000          # quick pass
python benchmarks/bench_sinks.py --rows 10000000        # full ~10M-row target (slow, multi-GB)
python benchmarks/bench_sinks.py --rows 1000000 --formats parquet avro --keep
```

## Development

This repo uses [`uv`](https://docs.astral.sh/uv/):

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy src      # types
```

## Status

- ✅ Template schema + loader (Pydantic, validated)
- ✅ Generation engine — generators + `SynthEngine`
- ✅ File sinks — json, csv, parquet, avro
- ⬜ More sinks (DB table, Kafka)
- ⬜ Airflow 3 operator (thin wrapper, emits an `Asset`, uses `airflow.io`)
- ⬜ Multi-table / linked-entity generation

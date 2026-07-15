# pysynthgen

Template-driven synthetic data generation engine.

`pysynthgen` takes a validated JSON template describing a dataset and yields synthetic
rows as an `Iterator[dict]`, writing them to a configurable sink (JSON, CSV, Parquet,
or Avro).

## Installation

```bash
pip install pysynthgen
```

That gives you the engine and the JSON and CSV sinks, which need nothing beyond the
standard library. Requires Python 3.10+.

The Parquet and Avro sinks depend on `pyarrow` and `fastavro`, which are **not**
installed by default — they are heavy, and most runs don't need them. Pick the extra
for the formats you want:

```bash
pip install "pysynthgen[parquet]"   # + pyarrow, enables the parquet sink
pip install "pysynthgen[avro]"      # + fastavro, enables the avro sink
pip install "pysynthgen[all]"       # both
```

The quotes matter in zsh, which would otherwise treat the brackets as a glob.

You only pay for what you install: `import pysynthgen` never imports pyarrow or
fastavro, and each sink imports its backend lazily when you build it. Asking for a
format whose extra is missing raises an error naming the extra to install.

## Design

- **Engine is standalone and dependency-light.** It takes a validated template and
  returns an iterator of rows; the heavy format dependencies are optional extras.
- **Templates are validated with Pydantic**, not raw dict parsing. Each field type
  is its own model in a discriminated union keyed on `type`, so validation is
  type-specific and mistakes are caught up front.
- **Generation is seeded and reproducible.** A single `seed` at the template level
  drives all randomness (numeric, Faker, regex), so a run is byte-for-byte
  repeatable. Output depends only on the template and its seed — never on how you
  consume it, so `iter_rows()` and `iter_batches(n)` agree for any `n`.
- **Generation is vectorized.** Rows are built a column at a time: each field's
  values are drawn for a whole chunk of rows in one numpy call, then transposed into
  row dicts. Field types that can't vectorize (`faker`, `regex`) fall back to a
  per-row draw.
- **Streaming = chunked generation.** The engine exposes `iter_rows()` and
  `iter_batches(batch_size)`; the consumer (a sink or a file writer) decides how to
  persist batches. The internal generation chunk is fixed, so peak memory stays flat
  however large `row_count` gets.
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
| `bool` | boolean | optional `true_probability` (default 0.5) |
| `sequence` | auto-increment counter | optional `start` (default 1), `step` (default 1) |
| `date` | date between bounds | `start`, `end` |
| `datetime` | datetime between bounds | `start`, `end` |
| `int` | integer | `distribution` (`uniform`/`normal`), `min`/`max` or `mean`/`stddev` |
| `float` | float | same as `int` |
| `decimal` | exact `Decimal` | `precision`, `scale`, plus the same params as `float` |
| `category` | value from a set | `values`, optional `weights` (sum to 1) |
| `faker` | Faker output | `provider` (e.g. `email`), optional `max_length` |
| `regex` | string matching a pattern | `pattern`, optional `max_length` |
| `reference` | copy of an earlier field in the same row | `field` |

String-producing fields (`faker`, `regex`) accept an optional `max_length` that
truncates the generated value. For `regex`, truncation may break the pattern match.

Use `decimal` rather than `float` for money: a float cannot represent most decimal
fractions exactly, and Parquet and Avro both carry a real decimal type that a float
field cannot reach. `precision` (total digits, max 18 — values are drawn as float64,
which carries no more) doubles as a hard bound, so no value can overflow the declared
type. CSV and JSON have no decimal type, so both emit the exact digits as a string.

`sequence` is the usual stand-in for an auto-increment primary key. It is the one
field that draws no randomness — its value follows from the row's position alone, so
adding one cannot change any other column, and the values stay contiguous even when a
`unique` constraint forces rows to be regenerated.

Every field also accepts `null_probability` (0–1): the chance of emitting `null`
for that field on a given row.

### Constraints

- `unique` — the listed field(s) form a unique key across generated rows.

### Generating templates with AI

If you use Claude Code, the bundled skill in
[`.claude/skills/pysynthgen-template/`](.claude/skills/pysynthgen-template/) writes
templates for you: describe the dataset in words, or point it at a sample file (CSV,
JSON, Parquet, Avro) and it infers the schema. It ships a profiler you can also run
directly:

```bash
python .claude/skills/pysynthgen-template/profile_sample.py sample.csv --rows 2000
```

## Usage

```python
from pysynthgen import load_and_validate_template, SynthEngine, build_sink

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
python -m pysynthgen template.json                     # validate + echo the normalized spec
python -m pysynthgen template.json --rows 20           # print 20 sample rows as JSON
python -m pysynthgen template.json --out data.parquet  # generate full dataset to a file
python -m pysynthgen template.json --out data --format avro
```

## Sinks

A sink consumes rows in batches and writes one output artifact. All sinks
implement `write_batch(rows)` / `finalize() -> path`, and `build_sink(format, path)`
selects one by name.

| format | extension | dependency |
|--------|-----------|------------|
| `json` | `.json` | stdlib (streamed JSON array) |
| `csv` | `.csv` | stdlib (`delimiter`/`quotechar` configurable) |
| `parquet` | `.parquet`, `.pq` | `pyarrow` — `pysynthgen[parquet]` |
| `avro` | `.avro` | `fastavro` — `pysynthgen[avro]` |

Notes: parquet/avro infer their schema from the first batch with all columns
nullable; avro writes naive datetimes as UTC so output is deterministic across
machines. Install both format deps with `pysynthgen[all]`.

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

`benchmarks/bench_generators.py` measures the generator draw itself, comparing a
per-row draw against the column-at-a-time draw the engine uses and checking the two
agree value-for-value:

```bash
python benchmarks/bench_generators.py --rows 200000
```

## Development

This repo uses [`uv`](https://docs.astral.sh/uv/):

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy src/pysynthgen benchmarks/  # types (strict)
```

## Contributing & releases

Development happens on short-lived branches merged into `main` via squash-merge, with
[Conventional Commit](https://www.conventionalcommits.org/) PR titles. Merging to
`main` runs an automated pipeline that computes the next semantic version, tags a
release, and publishes to PyPI. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

- ✅ Template schema + loader (Pydantic, validated)
- ✅ Generation engine — generators + `SynthEngine`
- ✅ File sinks — json, csv, parquet, avro
- ⬜ More sinks (DB table, Kafka)
- ⬜ Multi-table / linked-entity generation

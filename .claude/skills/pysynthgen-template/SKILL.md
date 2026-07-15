---
name: pysynthgen-template
description: >-
  Generate a pysynthgen synthetic-data template (the JSON that drives
  `pysynthgen`). Use when the user wants to create, design, or scaffold a
  pysynthgen template — either from a natural-language description of the dataset
  they want, or by inferring the schema from an attached/sample data file
  (CSV, JSON, JSONL, Parquet, or Avro). Triggers on requests like "make a
  pysynthgen template", "generate synthetic data config", "build a template from
  this CSV/parquet/avro sample", or "describe a users table and give me the
  template".
---

# pysynthgen template generator

Produce a valid [`pysynthgen`](https://github.com/mohsensafari/pysynthgen) template
(a JSON file) that the engine turns into synthetic rows. There are two entry points:

1. **From a description** — the user describes the dataset in words.
2. **From a sample file** — the user attaches a data file; infer the template from it.

Always end by **writing the template to a `.json` file** and **validating it** (see
[Validate & preview](#validate--preview)). Never hand back an unvalidated template.

## Template format (authoritative reference)

A template is one JSON object:

```json
{
  "row_count": 100000,
  "seed": 42,
  "fields": [ /* one object per column, in order */ ],
  "constraints": [ /* optional */ ]
}
```

- `row_count` (int > 0) — how many rows to generate.
- `seed` (int, optional) — makes generation reproducible. Always set one unless the
  user wants nondeterministic output.
- `fields` — ordered list of field specs (below). Order matters for `reference`.
- `constraints` — optional list; currently only `unique`.

**Every field** supports:
- `name` (string, required) — the column name.
- `null_probability` (0.0–1.0, optional, default 0.0) — chance the value is `null`.

### Field types

| `type` | Produces | Required params | Optional params |
|--------|----------|-----------------|-----------------|
| `uuid` | UUIDv4 string | — | — |
| `bool` | boolean | — | `true_probability` (default 0.5) |
| `sequence` | auto-increment counter | — | `start` (default 1), `step` (default 1) |
| `date` | date between bounds | `start`, `end` (`YYYY-MM-DD`) | — |
| `datetime` | datetime between bounds | `start`, `end` (ISO, e.g. `2024-01-01T00:00:00`) | — |
| `int` | integer | see below | — |
| `float` | float | see below | — |
| `decimal` | exact `Decimal` | `precision`, `scale` | same as `float` |
| `category` | value from a fixed set | `values` (non-empty list) | `weights` (list summing to 1.0) |
| `faker` | Faker output | `provider` (e.g. `email`) | `max_length` |
| `regex` | string matching a pattern | `pattern` | `max_length` |
| `reference` | copy of an earlier field in the same row | `field` (name of an earlier field) | — |

**`int` / `float`** take a `distribution`:
- `"uniform"` (default) → requires `min` and `max`.
- `"normal"` → requires `mean` and `stddev`; may add `min`/`max` as clamps.

```json
{"name": "age", "type": "int", "distribution": "normal", "mean": 35, "stddev": 10, "min": 18}
{"name": "score", "type": "float", "distribution": "uniform", "min": 0.0, "max": 1.0}
```

**`decimal`** — takes the same `distribution` params as `float`, plus `precision`
(total digits, 1–18) and `scale` (digits after the point, ≤ `precision`).

**Reach for `decimal` over `float` for money.** A float cannot represent most
decimal fractions exactly, and Parquet and Avro both carry a real decimal type that
a float field cannot reach. `precision` is also a hard bound — `precision` 5 /
`scale` 2 tops out at 999.99 — and a `min`/`max` outside it is rejected rather than
silently clamped, so size the digits to the range.

```json
{"name": "price", "type": "decimal", "precision": 8, "scale": 2, "min": 0.0, "max": 9999.99}
```

CSV and JSON have no decimal type, so both sinks emit the exact digits as a string;
Parquet and Avro give back real `Decimal` values.

**`bool`** — `true_probability` is the chance of `true` (default 0.5).

```json
{"name": "is_active", "type": "bool", "true_probability": 0.8}
```

**`sequence`** — the usual choice for an auto-increment integer primary key. Values
run `start`, `start + step`, ... for `row_count` rows; `step` may be negative.

It is the one field that draws no randomness — its value follows from the row's
position alone — so it needs **no `unique` constraint** (it cannot collide, and the
constraint would only cost retries).

```json
{"name": "order_id", "type": "sequence", "start": 1000, "step": 1}
```

**`category`** — `weights` (if given) must be the same length as `values` and sum to
1.0. Omit `weights` for a uniform distribution.

```json
{"name": "country", "type": "category", "values": ["US", "NL", "DE"], "weights": [0.5, 0.3, 0.2]}
```

**`faker`** — `provider` is any [Faker](https://faker.readthedocs.io/) method name:
`email`, `name`, `first_name`, `last_name`, `company`, `address`, `city`, `country`,
`phone_number`, `job`, `url`, `ipv4`, `user_name`, `sentence`, etc.

**`regex`** — `pattern` is a regular expression; the value is a string that matches
it. Good for structured IDs/codes: `"[A-Z]{3}-\\d{4}"`, `"[A-F0-9]{32}"`. (Escape
backslashes in JSON.) `max_length` truncates the result, which for `regex` may break
the match — only use it as a hard ceiling.

**`reference`** — copies the value of an **earlier** field in the same row (an
intra-row foreign key). The referenced `field` must appear before it in `fields`.

```json
{"name": "user_id", "type": "uuid"},
{"name": "referrer_id", "type": "reference", "field": "user_id", "null_probability": 0.7}
```

### Constraints

Only `unique` today — the listed field(s) form a unique key across all rows:

```json
"constraints": [{"type": "unique", "fields": ["user_id"]}]
```

Only add a `unique` constraint on fields whose domain is large enough for
`row_count` (e.g. `uuid`), otherwise generation fails after retries.

## Workflow A — from a description

1. Clarify only what you can't reasonably assume: `row_count`, and any domain
   specifics (value ranges, categories, locale). Pick sensible defaults otherwise
   and state them.
2. Map each described column to the **most specific** field type:
   - Opaque IDs / primary keys → `uuid` (+ a `unique` constraint).
   - Auto-increment / serial integer keys → `sequence` (no `unique` constraint).
   - Emails, names, addresses, phones, companies, URLs → `faker` with the matching
     provider.
   - Structured codes (SKU, license plate, hex token) → `regex`.
   - Dates/times → `date` or `datetime` with realistic `start`/`end`.
   - Money (price, amount, balance, salary) → `decimal`, not `float`.
   - Other quantities / measures → `int`/`float`; use `normal` when a bell curve is
     realistic (age, latency-ish), `uniform` otherwise.
   - Yes/no flags → `bool`, with `true_probability` if the user implies a skew.
   - Small fixed sets (status, plan, country subset) → `category`, with `weights` if
     the user implies a skew.
   - "Same as / points to another column" → `reference`.
3. Add `null_probability` where the user says a field is optional/sparse.
4. Set a `seed`. Write the file. Validate.

## Workflow B — from a sample file

The user attaches or points to a CSV, JSON/JSONL, Parquet, or Avro file. **Do not
guess from the filename** — inspect the data.

1. **Profile the file** with the bundled helper:

   ```bash
   python .claude/skills/pysynthgen-template/profile_sample.py <path-to-sample> --rows 2000
   ```

   It prints a per-column profile (inferred type, null %, cardinality, sample
   values, numeric/date ranges, category values + weights) **and a draft template
   JSON**. If Parquet/Avro deps are missing, install them: `pip install pyarrow fastavro`.

2. **Review and refine** the draft — the profiler is a starting point, not the final
   answer. Apply judgment:
   - A string column the profiler calls `regex` may really be a `faker` semantic type
     (emails, names, cities) — prefer the Faker provider when the meaning is clear.
   - Confirm `category` value lists aren't just an artifact of a tiny sample; if the
     real domain is larger, widen `values` or switch to `faker`/`regex`.
   - Set `row_count` to what the user wants (default: the sample's row count, or ask).
   - Add a `unique` constraint on columns that are keys (all-distinct id-like columns).
   - Keep the observed `null_probability`, but round to a clean value.
3. Write the file. Validate.

### Type-inference heuristics (for manual review or without the helper)

Per column, over a sample of rows:
- **All values are UUIDs** → `uuid`.
- **All booleans** → `bool`, with `true_probability` from the observed share.
- **All integers, no nulls, evenly spaced and ascending, with an id-like name** →
  `sequence` (`start` = first value, `step` = the spacing). An evenly spaced *measure*
  (a year, a bucketed count) is a real distribution — keep it as `int`.
- **All match an email/url/ip shape**, or the column name is clearly semantic
  (`email`, `name`, `city`, `phone`) → `faker` with that provider.
- **All parse as datetime with a time component** → `datetime` (`start`/`end` =
  observed min/max). **Date only** → `date`.
- **Values are stored `Decimal`s** (a Parquet/Avro decimal column), or the name is
  money-ish (`price`, `amount`, `total`, `balance`) and the values carry a small
  consistent number of decimal places → `decimal`, with `scale` = places observed and
  `precision` = integer digits + `scale`. Reading a stored decimal back as a `float`
  throws away the exactness the source took care to keep.
- **All integer** → `int`; if low-cardinality treat as `category` instead.
- **All numeric with decimals** → `float`; use `normal` (`mean`/`stddev`) if roughly
  bell-shaped, else `uniform` with observed `min`/`max`.
- **Low cardinality** (few distinct values, e.g. ≤ 20 and ≤ ~5% of rows) →
  `category`, with `weights` from observed frequencies.
- **Consistent fixed-length/charset strings** (codes, tokens) → `regex` with a
  derived pattern.
- **Free text / high-cardinality strings** → `faker` (`sentence`, `word`) or `regex`.
- **Null fraction** in the sample → `null_probability` (rounded).

## Validate & preview

After writing `<name>.json`, always validate and show a small sample:

```bash
python -m pysynthgen <name>.json            # validate + echo the normalized spec
python -m pysynthgen <name>.json --rows 5   # print 5 sample rows as JSON
```

If validation fails, read the error, fix the template, and re-run. Common issues:
- `category` `weights` don't sum to 1.0 or don't match `values` length.
- `reference.field` points to a field not defined earlier.
- `date`/`datetime` `start` after `end`.
- `int`/`float`/`decimal` missing the params its `distribution` requires.
- `decimal` `min`/`max` too big for the digits declared (`precision` 5 / `scale` 2
  holds ±999.99) — widen `precision`, or narrow the bounds.
- `decimal` `scale` greater than `precision`, or `precision` above 18.

Present the final validated template path and a couple of sample rows to the user.

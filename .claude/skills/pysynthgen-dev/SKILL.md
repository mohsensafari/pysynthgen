---
name: pysynthgen-dev
description: >-
  Working context for developing the pysynthgen library itself. Use whenever you
  are modifying this repo — adding or changing a field type, generator, sink, the
  engine, schema, CLI, tests, benchmarks, or CI/release config — or answering
  "how does pysynthgen work / how is it structured / how do I contribute / how do
  releases work" for a maintainer. Loads the architecture, conventions, dev
  commands, and branching/release policy so they don't need re-explaining.
---

# Developing pysynthgen

`pysynthgen` is a template-driven synthetic-data generation engine. A validated JSON
**template** describes a table; the **engine** yields `dict` rows lazily; **sinks**
write them to a file format. It is a standalone library (no Airflow, no framework).

## Repository map

```
src/pysynthgen/
  __init__.py     public API; __version__ read from installed metadata
  schema.py       Pydantic models: field types as a discriminated union + TemplateSpec
  loader.py       load_and_validate_template(dict | path | json-string) -> TemplateSpec
  generators.py   BaseGenerator (generate / generate_column), RandomBundle, @register REGISTRY
  engine.py       SynthEngine (iter_rows / iter_batches), column generation, uniqueness
  __main__.py     CLI: validate/echo, --rows sample, --out/--format/--batch-size
  sinks/
    base.py       BaseSink (write_batch, finalize, write), DEFAULT_BATCH_SIZE
    {json,csv,parquet,avro}_sink.py
    __init__.py   SINK_REGISTRY, build_sink(fmt, path), format_from_path(path)
tests/            test_schema, test_engine, test_sinks, test_benchmark
benchmarks/
  bench_sinks.py       sink throughput + peak-RSS (not in the default suite)
  bench_generators.py  per-row vs vectorized generator draw
.github/workflows/   ci.yml (PRs), release.yml (push to main)
CONTRIBUTING.md   the full branching + release policy
```

## Core concepts & invariants

- **Schema is a Pydantic v2 discriminated union** on the field `type` string
  (`schema.py`). `_FieldBase` gives every field `name` + `null_probability`;
  `_StringFieldBase` adds `max_length` for string-producing fields. `TemplateSpec`
  does cross-field validation (unique names, `reference` targets an *earlier* field,
  constraints reference known fields). Field types: `uuid`, `date`, `datetime`,
  `int`, `float`, `category`, `faker`, `regex`, `reference`.
- **Determinism is a hard invariant.** A single template `seed` drives everything.
  All randomness MUST come from the `RandomBundle` (`np_rng` numpy Generator, a
  seeded `Faker`, a seeded `rstr.Rstr`). Never use `random`, `os.urandom`, or
  `uuid.uuid4()` directly — e.g. the uuid generator builds a v4 UUID from
  `np_rng.bytes(16)`. Two engines from the same spec must yield identical rows;
  tests enforce this.
- **The generator registry is the extension point.** `@register("type")` maps a
  `type` string to a `BaseGenerator` subclass; `build_generator` instantiates it.
  `generate(row)` receives the partially-built row (so `reference` can read earlier
  fields) and is all a new generator must implement.
- **The engine generates a column at a time.** `_generate_chunk(n)` asks each
  generator for a whole column via `generate_column(n, columns)`, then transposes
  into row dicts. `BaseGenerator.generate_column` defaults to `n` per-row `generate`
  calls (each handed a partial row rebuilt from `columns`), so a generator that only
  implements `generate` still works; the numpy/uuid-bound types override it to draw
  the column in one call. Vectorize only what stays value-identical to the per-row
  draw — `benchmarks/bench_generators.py` checks exactly that.
- **The generation chunk is fixed** (`_GENERATION_CHUNK`), deliberately *not* the
  caller's batch size: output must depend only on the seed, never on how rows are
  consumed, and peak memory must stay flat as `row_count` grows. `iter_rows()` and
  `iter_batches(n)` must agree for every `n` — changing this constant changes every
  template's output.
- **null_probability must not perturb the RNG stream.** The engine only draws a
  nullability random when `null_probability > 0`, so a field left at the default
  costs no randomness. Within a chunk the value column is drawn in full and *then*
  masked, so a nulled cell still consumes its value draw.
- **Sinks are streaming.** `BaseSink.write(rows, batch_size=500)` chunks an iterator
  through `write_batch` then `finalize`. Parquet/Avro infer schema from the first
  batch (all columns nullable) and import their heavy deps lazily, so `import
  pysynthgen` never pulls in pyarrow/fastavro.
- **Uniqueness**: `unique` constraints regenerate the whole row up to 100 times, then
  raise `GenerationError`. Regeneration is the per-row path (`_generate_once`), run
  only on rows that actually collide, so the vectorized path stays the common case.

## Dev environment & commands

Uses [`uv`](https://docs.astral.sh/uv/). Optional deps are extras: `parquet`
(pyarrow), `avro` (fastavro), `all`, `dev`.

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest -q                                    # tests
uv run ruff check .                                 # lint (whole tree, incl .claude/, .github/)
uv run mypy src/pysynthgen benchmarks/          # types (strict)
uv run pysynthgen path/to/template.json --rows 5    # exercise the CLI
```

Before pushing, all three (pytest, ruff, mypy) must be clean — CI runs them on
Python 3.10–3.14.

## Conventions

- Python ≥ 3.10, `from __future__ import annotations` at the top of every module,
  full type hints. mypy is **strict** and must pass.
- ruff: line length 100; rules `E, F, I, UP, B`.
- Match the surrounding code's density and idiom. Docstrings are terse and explain
  *why*; comments are sparse and only where intent isn't obvious.
- Keep the engine framework-free — no new heavy runtime deps in the core; put
  optional backends behind extras and lazy imports.

### Adding a field type

1. `schema.py`: add a model extending `_FieldBase` (or `_StringFieldBase`) with
   `type: Literal["x"]`, its params, and validators; add it to the `FieldSpec` union.
2. `__init__.py`: export the new model.
3. `generators.py`: add a `@register("x")` `BaseGenerator` subclass implementing
   `generate(row)`; draw only from `self.rng`. If the draw vectorizes, also override
   `generate_column(n, columns)` to draw the whole column in one numpy call — it must
   produce the same values the per-row `generate` would for the same seed; add the
   type to `benchmarks/bench_generators.py`, which asserts that.
4. Tests: validation cases in `test_schema.py`, behaviour + a determinism check in
   `test_engine.py`.
5. Update the field table in `README.md`.

### Adding a sink

1. New `sinks/<fmt>_sink.py` with a `BaseSink` subclass; implement `write_batch` and
   `finalize`; lazily import any heavy dep inside `__init__` with a clear error.
2. Register it in `sinks/__init__.py` (`SINK_REGISTRY`, `_EXTENSIONS`) and export.
3. If it needs a heavy dep, add an extra in `pyproject.toml` (+ a mypy override if
   the lib is untyped) and to the `dev` extra.
4. Add parametrized contract tests in `test_sinks.py` (round-trip row count, null
   survival, read-back types).
5. Update the sinks table in `README.md`.

## Branching & release policy

See `CONTRIBUTING.md` for the authoritative version. Summary:

- **Trunk-based.** `main` is protected and always releasable. Work on short-lived
  branches named by type: `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`.
- **Open a PR into `main`; squash-merge.** The squash commit message = the PR title,
  so **PR titles must be valid [Conventional Commits](https://www.conventionalcommits.org/)**
  (CI enforces this). CI (ruff, mypy, pytest 3.10–3.14) must pass.
- **Never tag or publish by hand.** Merging to `main` runs `release.yml`:
  python-semantic-release reads commit types since the last tag and, if
  release-worthy, bumps `[project].version`, updates `CHANGELOG.md`, tags `vX.Y.Z`,
  creates a GitHub Release, and publishes to PyPI via Trusted Publishing (OIDC).

Version impact by type (pre-1.0, `major_on_zero = false`):

| Type | Bump |
|------|------|
| `fix:` | patch |
| `feat:` | minor |
| `perf:` | patch |
| `feat!:` / `BREAKING CHANGE:` | minor (would be major once ≥ 1.0) |
| `docs:` `chore:` `refactor:` `test:` `ci:` `build:` `style:` | **no release** |

So: to ship a change, use `fix:`/`feat:`. For a change that must merge but must NOT
release (docs, tooling, CI), use `docs:`/`chore:`/`ci:`.

- **Version is single-sourced** in `pyproject.toml`; `__version__` reads it via
  `importlib.metadata`. Never hardcode a version elsewhere.
- Commit messages carry no co-author trailers.

## Project-specific gotchas

- **CI + uv:** `astral-sh/setup-uv` already creates/activates a `.venv`. Do NOT run
  `uv venv` in CI (it errors "already exists"); use `uv pip install -e ".[dev]"` then
  `uv run --no-sync <tool>`. Never `uv pip install --system` (PEP 668 blocks the
  runner's system Python).
- **mypy vs numpy stubs:** numpy's modern stubs use PEP 695 `type` statements that
  mypy only parses under a 3.12+ target; a `numpy.*` `follow_imports = skip` override
  keeps the 3.10 target valid. Don't remove it.
- **regex generator:** rstr caps every repeat at a module-global limit (default 100),
  which crashes on fixed repeats > 100. `RegexGenerator` parses each pattern's
  largest explicit repeat and raises the cap per field; unbounded `+`/`*` stay capped.
- **Avro determinism:** naive datetimes are written as UTC so output doesn't depend on
  the host timezone.
- **Releases push to protected `main`:** `enforce_admins` is off and the workflow uses
  a `RELEASE_TOKEN` PAT (preferred over `GITHUB_TOKEN`) to push the release commit.
- **Related skill:** `.claude/skills/pysynthgen-template/` generates *templates* for
  end users; this skill is for developing the library itself.

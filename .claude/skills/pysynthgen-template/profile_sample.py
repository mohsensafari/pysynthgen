#!/usr/bin/env python3
"""Profile a sample data file and emit a draft pysynthgen template.

Reads a sample of rows from a CSV / JSON / JSONL / Parquet / Avro file, infers a
field type per column, and prints both a human-readable profile and a draft
pysynthgen template JSON. The draft is a starting point — review it against the
heuristics in SKILL.md before using.

    python profile_sample.py data.csv --rows 2000

Parquet needs `pyarrow`; Avro needs `fastavro` (both in `pysynthgen[all]`).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import uuid as uuidlib
from collections import Counter
from datetime import date, datetime
from itertools import islice
from pathlib import Path
from typing import Any

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
INT_RE = re.compile(r"^-?\d+$")

NAME_HINTS = {
    "email": "email", "e_mail": "email", "mail": "email",
    "name": "name", "full_name": "name", "fullname": "name",
    "first_name": "first_name", "firstname": "first_name",
    "last_name": "last_name", "lastname": "last_name", "surname": "last_name",
    "username": "user_name", "user_name": "user_name",
    "phone": "phone_number", "phone_number": "phone_number", "mobile": "phone_number",
    "address": "address", "street": "street_address",
    "city": "city", "company": "company", "url": "url", "website": "url",
    "job": "job", "title": "job", "ip": "ipv4", "ip_address": "ipv4",
}

# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #


def load_rows(path: Path, limit: int) -> tuple[list[str], list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(islice(reader, limit))
            cols = list(reader.fieldnames or (rows[0].keys() if rows else []))
        return cols, rows
    if suffix in (".json", ".jsonl", ".ndjson"):
        rows = _load_json(path, limit)
        return _columns(rows), rows
    if suffix in (".parquet", ".pq"):
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        rows = table.slice(0, limit).to_pylist()
        return list(table.schema.names), rows
    if suffix == ".avro":
        import fastavro

        with path.open("rb") as fh:
            rows = list(islice(fastavro.reader(fh), limit))
        return _columns(rows), rows
    raise SystemExit(f"unsupported file type: {suffix!r} (use csv/json/jsonl/parquet/avro)")


def _load_json(path: Path, limit: int) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        return [r for r in data[:limit] if isinstance(r, dict)]
    except json.JSONDecodeError:
        # JSONL: one object per line
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
            if len(rows) >= limit:
                break
        return [r for r in rows if isinstance(r, dict)]


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


# --------------------------------------------------------------------------- #
# Value helpers                                                               #
# --------------------------------------------------------------------------- #


def is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def as_number(v: Any) -> float | int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if INT_RE.match(s):
            return int(s)
        try:
            return float(s)
        except ValueError:
            return None
    return None


def as_temporal(v: Any) -> tuple[str, datetime] | None:
    if isinstance(v, datetime):
        return "datetime", v
    if isinstance(v, date):
        return "date", datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return ("datetime" if ":" in s else "date"), dt
    return None


def is_uuid(v: Any) -> bool:
    if isinstance(v, uuidlib.UUID):
        return True
    return isinstance(v, str) and bool(UUID_RE.match(v.strip()))


# --------------------------------------------------------------------------- #
# Inference                                                                   #
# --------------------------------------------------------------------------- #


def infer_field(name: str, values: list[Any], n_total: int, max_categories: int) -> dict[str, Any]:
    nonnull = [v for v in values if not is_null(v)]
    nulls = n_total - len(nonnull)
    field: dict[str, Any] = {"name": name}

    if not nonnull:
        field.update(type="faker", provider="word")
        _add_null(field, nulls, n_total)
        return field

    counter = Counter(str(v) for v in nonnull)
    cardinality = len(counter)

    if all(is_uuid(v) for v in nonnull):
        field["type"] = "uuid"
    elif all(isinstance(v, bool) for v in nonnull):
        _fill_category(field, counter)
    elif (temporal := _all_temporal(nonnull)) is not None:
        kind, lo, hi = temporal
        field["type"] = kind
        field["start"] = _fmt_temporal(kind, lo)
        field["end"] = _fmt_temporal(kind, hi)
    elif cardinality <= max_categories and cardinality <= max(1, len(nonnull) // 2):
        _fill_category(field, counter)
    elif all(as_number(v) is not None for v in nonnull):
        _fill_numeric(field, [as_number(v) for v in nonnull])
    elif (provider := _semantic_provider(name, nonnull)) is not None:
        field.update(type="faker", provider=provider)
    else:
        _fill_string(field, nonnull)

    _add_null(field, nulls, n_total)
    return field


def _all_temporal(values: list[Any]) -> tuple[str, datetime, datetime] | None:
    parsed = [as_temporal(v) for v in values]
    if any(p is None for p in parsed):
        return None
    kind = "datetime" if any(k == "datetime" for k, _ in parsed) else "date"  # type: ignore[misc]
    dts = [dt for _, dt in parsed]  # type: ignore[misc]
    return kind, min(dts), max(dts)


def _fmt_temporal(kind: str, dt: datetime) -> str:
    return dt.date().isoformat() if kind == "date" else dt.replace(microsecond=0).isoformat()


def _fill_category(field: dict[str, Any], counter: Counter) -> None:
    items = counter.most_common()
    total = sum(counter.values())
    field["type"] = "category"
    field["values"] = [v for v, _ in items]
    counts = [c for _, c in items]
    if counts and min(counts) * 1.5 < max(counts):  # skewed -> keep weights
        weights = [round(c / total, 4) for c in counts]
        weights[0] = round(weights[0] + (1.0 - sum(weights)), 4)
        field["weights"] = weights


def _fill_numeric(field: dict[str, Any], nums: list[Any]) -> None:
    if all(isinstance(n, int) for n in nums):
        field.update(type="int", distribution="uniform", min=min(nums), max=max(nums))
    else:
        lo, hi = float(min(nums)), float(max(nums))
        field.update(type="float", distribution="uniform", min=round(lo, 6), max=round(hi, 6))


def _semantic_provider(name: str, values: list[Any]) -> str | None:
    strs = [str(v).strip() for v in values]
    if all(EMAIL_RE.match(s) for s in strs):
        return "email"
    if all(URL_RE.match(s) for s in strs):
        return "url"
    if all(IPV4_RE.match(s) for s in strs):
        return "ipv4"
    return NAME_HINTS.get(re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"))


def _fill_string(field: dict[str, Any], values: list[Any]) -> None:
    strs = [str(v) for v in values]
    lengths = {len(s) for s in strs}
    charset = set("".join(strs))
    cls = ""
    if any(c.isupper() for c in charset):
        cls += "A-Z"
    if any(c.islower() for c in charset):
        cls += "a-z"
    if any(c.isdigit() for c in charset):
        cls += "0-9"
    seps = sorted(c for c in charset if not c.isalnum())
    only_simple = all(len(s) <= 40 for s in strs) and cls and len(seps) <= 2
    if only_simple:
        cls += "".join(re.escape(c) for c in seps)
        quant = f"{{{min(lengths)}}}" if len(lengths) == 1 else f"{{{min(lengths)},{max(lengths)}}}"
        field.update(type="regex", pattern=f"[{cls}]{quant}")
    else:
        avg = sum(len(s) for s in strs) / len(strs)
        field.update(type="faker", provider="sentence" if avg > 20 else "word")


def _add_null(field: dict[str, Any], nulls: int, n_total: int) -> None:
    if nulls and n_total:
        field["null_probability"] = round(nulls / n_total, 3)


# --------------------------------------------------------------------------- #
# Output                                                                      #
# --------------------------------------------------------------------------- #


def build_template(fields: list[dict[str, Any]], n_rows: int) -> dict[str, Any]:
    template: dict[str, Any] = {"row_count": n_rows or 1000, "seed": 42, "fields": fields}
    constraints = [
        {"type": "unique", "fields": [f["name"]]}
        for f in fields
        if f["type"] == "uuid" and not f.get("null_probability")
    ]
    if constraints:
        template["constraints"] = constraints
    return template


def print_profile(
    cols: list[str], rows: list[dict[str, Any]], fields: list[dict[str, Any]]
) -> None:
    n = len(rows)
    print(f"=== COLUMN PROFILE (sampled {n} rows) ===", file=sys.stderr)
    for name, field in zip(cols, fields, strict=True):
        vals = [r.get(name) for r in rows]
        nn = [v for v in vals if not is_null(v)]
        distinct = len({str(v) for v in nn})
        sample = ", ".join(str(v) for v in list(dict.fromkeys(str(x) for x in nn))[:3])
        extra = {k: v for k, v in field.items() if k not in ("name", "type")}
        print(
            f"  {name:<24} type={field['type']:<9} distinct={distinct:<6} "
            f"nulls={n - len(nn):<5} e.g. [{sample}]  -> {extra}",
            file=sys.stderr,
        )
    print(file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Sample file (csv/json/jsonl/parquet/avro).")
    parser.add_argument("--rows", type=int, default=2000, help="Max rows to sample.")
    parser.add_argument("--max-categories", type=int, default=20,
                        help="Cardinality at/below which a column becomes a category.")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"no such file: {path}")

    cols, rows = load_rows(path, args.rows)
    if not rows:
        raise SystemExit("file has no rows to profile")

    fields = [
        infer_field(name, [r.get(name) for r in rows], len(rows), args.max_categories)
        for name in cols
    ]
    print_profile(cols, rows, fields)
    print("=== DRAFT pysynthgen TEMPLATE (review before use) ===", file=sys.stderr)
    print(json.dumps(build_template(fields, len(rows)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

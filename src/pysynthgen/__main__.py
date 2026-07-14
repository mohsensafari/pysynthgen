"""Command-line entry point.

Modes:

* no flags       — validate the template and echo the normalized spec.
* ``--rows N``   — generate N sample rows and print them as JSON.
* ``--out PATH`` — generate the full dataset to a file. The format is taken from
  ``--format`` or inferred from the file extension.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date, datetime
from typing import Any

from pysynthgen.engine import SynthEngine
from pysynthgen.loader import TemplateError, load_and_validate_template
from pysynthgen.sinks import SINK_REGISTRY, build_sink, format_from_path


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return str(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pysynthgen", description=__doc__)
    parser.add_argument("template", help="Path to a JSON template file.")
    parser.add_argument(
        "--rows",
        type=int,
        default=None,
        metavar="N",
        help="Generate N sample rows to stdout instead of echoing the spec.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Generate the full dataset to this file.",
    )
    parser.add_argument(
        "--format",
        choices=sorted(SINK_REGISTRY),
        default=None,
        help="Output format for --out (default: inferred from the file extension).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10_000,
        help="Rows per write batch when using --out (default: 10000).",
    )
    args = parser.parse_args(argv)

    try:
        spec = load_and_validate_template(args.template)
    except TemplateError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.out is not None:
        return _generate_to_file(spec, args)
    if args.rows is not None:
        return _print_sample(spec, args.rows)

    print(spec.model_dump_json(indent=2))
    return 0


def _print_sample(spec: Any, n: int) -> int:
    engine = SynthEngine(spec)
    rows = []
    for i, row in enumerate(engine.iter_rows()):
        if i >= n:
            break
        rows.append(row)
    print(json.dumps(rows, indent=2, default=_json_default))
    return 0


def _generate_to_file(spec: Any, args: argparse.Namespace) -> int:
    try:
        fmt = args.format or format_from_path(args.out)
        sink = build_sink(fmt, args.out)
    except (ValueError, ImportError) as exc:
        print(exc, file=sys.stderr)
        return 1

    engine = SynthEngine(spec)
    path = sink.write(engine.iter_rows(), batch_size=args.batch_size)
    print(f"wrote {spec.row_count} rows ({fmt}) to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

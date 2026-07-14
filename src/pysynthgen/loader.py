"""Load and validate templates from a file path, raw JSON string, or dict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pysynthgen.schema import TemplateSpec


class TemplateError(ValueError):
    """Raised when a template is malformed or fails validation."""


def load_and_validate_template(source: str | Path | dict[str, Any]) -> TemplateSpec:
    """Return a validated :class:`TemplateSpec` from a dict, path, or JSON string.

    ``source`` may be:

    * a mapping — an already-parsed template, validated as-is;
    * a :class:`~pathlib.Path` — read as a JSON file;
    * a :class:`str` — treated as raw JSON when it looks like a JSON object
      (starts with ``{``), otherwise as a path to a JSON file.

    Any read/parse/validation failure is re-raised as :class:`TemplateError` with a
    readable message rather than a raw traceback.
    """
    if isinstance(source, dict):
        data: Any = source
    elif isinstance(source, Path):
        data = _load_json_file(source)
    else:  # str: disambiguate raw JSON from a path
        if source.lstrip().startswith("{"):
            data = _parse_json(source, origin="template string")
        else:
            data = _load_json_file(Path(source))

    try:
        return TemplateSpec.model_validate(data)
    except ValidationError as exc:
        raise TemplateError(f"invalid template:\n{exc}") from exc


def _load_json_file(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"cannot read template {path}: {exc}") from exc
    return _parse_json(raw, origin=str(path))


def _parse_json(raw: str, *, origin: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{origin} is not valid JSON: {exc}") from exc

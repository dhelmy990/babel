"""Load and validate versioned distillation data contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError, validators


_SCHEMA_DIRECTORY = Path(__file__).resolve().parents[3] / "schemas"


def _is_finite_json_number(checker: Any, instance: object) -> bool:
    return isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(instance)


_FiniteNumberValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("number", _is_finite_json_number),
)


def load_schema(name: str) -> dict:
    """Load a checked-in JSON Schema by its versioned contract name."""
    path = _SCHEMA_DIRECTORY / f"{name}.json"
    with path.open(encoding="utf-8") as schema_file:
        return json.load(schema_file)


def validate_document(schema_name: str, value: Mapping[str, object]) -> None:
    """Validate a document against a checked-in schema.

    JSON permits neither NaN nor infinities; the custom number type enforces
    that rule in addition to the JSON Schema document constraints.
    """
    _FiniteNumberValidator(load_schema(schema_name)).validate(dict(value))

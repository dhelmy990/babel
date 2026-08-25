"""Load and validate versioned distillation data contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, validators


_SCHEMA_NAMES = frozenset(
    {
        "dataset-readiness-v1",
        "dataset-manifest-v1",
        "distillation-example-v1",
        "full-release-proof-v1",
        "provenance-v1",
    }
)


class UnknownSchema(ValueError):
    """Raised when a caller requests a schema outside the public registry."""


def _is_finite_json_number(checker: Any, instance: object) -> bool:
    return (
        isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and math.isfinite(instance)
    )


_FiniteNumberValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("number", _is_finite_json_number),
)


def _require_known_schema(name: str) -> None:
    if not isinstance(name, str) or name not in _SCHEMA_NAMES:
        raise UnknownSchema(f"unknown schema: {name!r}")


@cache
def _load_schema(name: str) -> dict[str, Any]:
    _require_known_schema(name)
    path = files("babel_data").joinpath("schemas", f"{name}.json")
    with path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _validator(name: str) -> Draft202012Validator:
    _require_known_schema(name)
    return _FiniteNumberValidator(
        _load_schema(name),
        format_checker=FormatChecker(),
    )


def load_schema(name: str) -> dict:
    """Load a checked-in JSON Schema by its versioned contract name."""
    _require_known_schema(name)
    return deepcopy(_load_schema(name))


def validate_document(schema_name: str, value: Mapping[str, object]) -> None:
    """Validate a document against a checked-in schema.

    JSON permits neither NaN nor infinities; the custom number type enforces
    that rule in addition to the JSON Schema document constraints.
    """
    _validator(schema_name).validate(dict(value))

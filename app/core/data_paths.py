"""Read values out of provider responses by dot path.

Flow configurations name response fields as ``data.address.city`` or
``items.0.id``. Dict keys and list indexes are both supported; anything
else (attribute access, wildcards, expressions) is deliberately not.

:func:`describe_paths` goes the other way: it lists the paths a
response contains, with their JSON types, so the fields a provider
returns can be recorded without recording their values.
"""

import re
from typing import Any

MISSING: Any = object()
"""Returned when a path does not exist; distinct from a stored ``None``."""

# Keys usable in a dot path; others (with dots, spaces...) are skipped.
_PATH_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_DESCRIBED_DEPTH = 8
MAX_DESCRIBED_PATHS = 300


def read_path(data: Any, path: str) -> Any:
    """Return the value at ``path`` in ``data``, or :data:`MISSING`."""
    current = data
    for key in path.split("."):
        if isinstance(current, dict):
            if key not in current:
                return MISSING
            current = current[key]
        elif isinstance(current, list) and key.isdigit():
            index = int(key)
            if index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def json_type(value: Any) -> str:
    """Return the JSON type name of a decoded JSON value."""
    # bool is checked before int: in Python, True is an int.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def describe_paths(data: Any) -> dict[str, str]:
    """Return ``{dot path: JSON type}`` for every field in ``data``.

    Nested objects are walked (``address.zip``); arrays are listed as
    ``array`` without walking their items, so a long list does not turn
    into one path per element. Only keys and types are returned, never
    values. Empty objects are listed as ``object``. Keys that cannot be
    used in a dot path are skipped, and output stops at
    :data:`MAX_DESCRIBED_PATHS` paths and :data:`MAX_DESCRIBED_DEPTH`
    levels, so a malformed response cannot grow it without bound.
    """
    paths: dict[str, str] = {}
    if isinstance(data, dict):
        _walk(data, "", 1, paths)
    return paths


def _walk(
    node: dict[str, Any], prefix: str, depth: int, paths: dict[str, str]
) -> None:
    for key, value in node.items():
        if len(paths) >= MAX_DESCRIBED_PATHS:
            return
        if not isinstance(key, str) or not _PATH_KEY.fullmatch(key):
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value and depth < MAX_DESCRIBED_DEPTH:
            _walk(value, f"{path}.", depth + 1, paths)
        else:
            paths[path] = json_type(value)

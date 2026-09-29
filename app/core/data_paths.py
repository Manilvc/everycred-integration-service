"""Read values out of provider responses by dot path.

Flow configurations name response fields as ``data.address.city`` or
``items.0.id``. Dict keys and list indexes are both supported; anything
else (attribute access, wildcards, expressions) is deliberately not.
"""

from typing import Any

MISSING: Any = object()
"""Returned when a path does not exist; distinct from a stored ``None``."""


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

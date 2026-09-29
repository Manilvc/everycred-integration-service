"""Placeholder substitution for configuration-driven connectors.

Tool configurations describe requests with placeholders instead of
code, for example::

    {"id_number": "{kwargs.id_number}", "consent": "Y"}

Supported placeholders:

========================  ==========================================
``{kwargs.<name>}``       A keyword argument from the caller
``{args.<index>}``        A positional argument, counted from 0
``{credentials.<name>}``  The client's stored credential for the tool
``{settings.<name>}``     The client's non-secret setting
``{user_uuid}``           The client's id for the user
``{client_id}``           The client project's id
========================  ==========================================

A string that is exactly one placeholder is replaced by the value
itself, keeping its JSON type (numbers stay numbers). A placeholder
inside a longer string is converted to text. Nothing is ever evaluated
as code, and unknown placeholders are errors rather than empty strings.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from app.connectors.base import MissingParametersError

_PLACEHOLDER = re.compile(r"\{([a-z_]+)(?:\.([A-Za-z0-9_]+))?\}")
_SOURCES_WITH_KEY = {"kwargs", "args", "credentials", "settings"}
_SOURCES_WITHOUT_KEY = {"user_uuid", "client_id"}


class TemplateSyntaxError(ValueError):
    """Raised when a template uses an unknown placeholder form."""


def find_placeholders(template: Any) -> set[str]:
    """Return every placeholder in ``template``, e.g. ``kwargs.pan``.

    Walks nested dicts and lists. Used both to validate configurations
    and to tell callers which inputs an operation needs.

    Raises:
        TemplateSyntaxError: A placeholder has an unknown source, or is
            missing or has a key it should not have.
    """
    found: set[str] = set()
    for text in _strings_in(template):
        for match in _PLACEHOLDER.finditer(text):
            source, key = match.group(1), match.group(2)
            if source in _SOURCES_WITH_KEY and key:
                found.add(f"{source}.{key}")
            elif source in _SOURCES_WITHOUT_KEY and key is None:
                found.add(source)
            else:
                raise TemplateSyntaxError(
                    f"unknown placeholder '{match.group(0)}'"
                )
    return found


def render(template: Any, values: Mapping[str, Any]) -> Any:
    """Return ``template`` with every placeholder replaced.

    Args:
        template: A string, or dicts and lists containing strings.
        values: Sources keyed by name: ``kwargs``, ``credentials``, and
            ``settings`` map to dicts, ``args`` to a list, and
            ``user_uuid`` / ``client_id`` to plain values.

    Raises:
        MissingParametersError: Placeholders whose value is missing,
            listed together so the caller can fix them in one go.
    """
    missing: list[str] = []
    rendered = _render(template, values, missing)
    if missing:
        raise MissingParametersError(sorted(set(missing)))
    return rendered


def _render(template: Any, values: Mapping[str, Any], missing: list[str]):
    if isinstance(template, dict):
        return {
            key: _render(item, values, missing)
            for key, item in template.items()
        }
    if isinstance(template, list):
        return [_render(item, values, missing) for item in template]
    if not isinstance(template, str):
        return template

    whole_match = _PLACEHOLDER.fullmatch(template)
    if whole_match:
        return _lookup(whole_match, values, missing)

    def replace(match: re.Match[str]) -> str:
        value = _lookup(match, values, missing)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(replace, template)


def _lookup(
    match: re.Match[str], values: Mapping[str, Any], missing: list[str]
) -> Any:
    source, key = match.group(1), match.group(2)
    name = f"{source}.{key}" if key else source
    container = values.get(source)
    if key is None:
        if container is None:
            missing.append(name)
        return container
    if source == "args":
        index = int(key) if key.isdigit() else -1
        if isinstance(container, Sequence) and 0 <= index < len(container):
            return container[index]
    elif isinstance(container, Mapping) and key in container:
        return container[key]
    missing.append(name)
    return None


def _strings_in(template: Any):
    if isinstance(template, str):
        yield template
    elif isinstance(template, dict):
        for item in template.values():
            yield from _strings_in(item)
    elif isinstance(template, list):
        for item in template:
            yield from _strings_in(item)


def url_encode_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``values`` with every leaf percent-encoded for a URL path.

    Used for anything rendered into a path, so an input such as
    ``../admin`` cannot change which endpoint is called.
    """

    def encode(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: encode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [encode(item) for item in value]
        return quote(str(value), safe="") if value is not None else None

    return {key: encode(value) for key, value in values.items()}

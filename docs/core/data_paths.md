# data_paths

> Module: `app/core/data_paths.py`
> Last updated: 2026-10-02

## Purpose

Flow configurations name values inside provider responses by **dot
path**: `full_name`, `org.department`, `items.0.id`. `read_path` is
the one place those paths are resolved, for captured values, outputs
and `verified_when` conditions.

## API

```python
from app.core.data_paths import MISSING, read_path

read_path({"org": {"department": "Finance"}}, "org.department")  # "Finance"
read_path({"items": [{"id": 7}]}, "items.0.id")  # 7
read_path({"a": None}, "a")  # None
read_path({}, "a.b") is MISSING  # True
```

- Dict keys and list indexes (digits) only. No wildcards, expressions or
  attribute access, so a configuration can never run code.
- `MISSING` is a sentinel distinct from `None`, so "the provider sent
  null" and "the provider did not send it" stay different. Outputs
  that are `MISSING` are left out of a session's result.

## Describing a response

`describe_paths(data)` returns `{dot path: JSON type}` for every field
of a response object, never the values:

```python
describe_paths({"full_name": "…", "address": {"zip": "…"}, "docs": [1, 2]})
# {"full_name": "string", "address.zip": "string", "docs": "array"}
```

- Objects are walked; arrays are reported as `array` without walking
  their items.
- Keys that cannot appear in a dot path (dots, spaces) are skipped.
- At most `MAX_DESCRIBED_PATHS` (300) paths and `MAX_DESCRIBED_DEPTH`
  (8) levels, so a malformed response cannot grow the output.
- `json_type(value)` gives the type names used: `string`, `number`,
  `boolean` (checked before `number`, since `True` is an `int`),
  `object`, `array`, `null`.

## Where it is used

- `app/features/sessions/service.py`: `capture`, `outputs`,
  `verified_when`, and recording the keys of completed sessions'
  responses with `describe_paths`.

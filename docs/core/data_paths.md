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

## Where it is used

- `app/features/sessions/service.py`: `capture`, `outputs`,
  `verified_when`.

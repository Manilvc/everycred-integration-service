"""Per-request context shared across the call stack.

Values here are stored in :mod:`contextvars`, so each request (and each
task spawned from it) sees its own copy without passing them through
every function signature.
"""

from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

request_id_context: ContextVar[str | None] = ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """Return the current request's id, or None outside a request."""
    return request_id_context.get()

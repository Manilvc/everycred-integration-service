"""Errors raised by the integration types feature."""

from app.core.exceptions import NotFoundError


class IntegrationTypeNotFoundError(NotFoundError):
    """Raised when no integration type has the requested code."""

    error_code = "integration_type_not_found"

    def __init__(self, code: str) -> None:
        super().__init__(f"Integration type '{code}' does not exist.")

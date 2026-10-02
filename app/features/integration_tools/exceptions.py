"""Errors raised by the integration tools feature."""

from fastapi import status

from app.core.exceptions import AppError, NotFoundError


class IntegrationToolNotFoundError(NotFoundError):
    """Raised when no integration tool has the requested code."""

    error_code = "integration_tool_not_found"

    def __init__(self, code: str) -> None:
        super().__init__(f"Integration tool '{code}' does not exist.")


class IntegrationToolNotUsableError(AppError):
    """Raised when a tool cannot be chosen for an integration type."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "integration_tool_not_usable"


class UnknownIntegrationTypesError(AppError):
    """Raised when a tool definition names types that do not exist."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "unknown_integration_types"

    def __init__(self, codes: list[str]) -> None:
        super().__init__(
            "Unknown integration types: " + ", ".join(sorted(codes)) + "."
        )


class UnknownToolFlowsError(AppError):
    """Raised when fields name flows the tool does not have."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "unknown_tool_flows"

    def __init__(self, tool_code: str, flows: list[str]) -> None:
        super().__init__(
            f"Tool '{tool_code}' has no flows named: "
            + ", ".join(sorted(flows))
            + "."
        )

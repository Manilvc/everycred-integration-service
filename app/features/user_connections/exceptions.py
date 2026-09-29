"""Errors raised by the user connections feature."""

from fastapi import status

from app.core.exceptions import (
    AppError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)


class ConnectionNotFoundError(NotFoundError):
    """Raised when the user has no connection for the integration type."""

    error_code = "connection_not_found"

    def __init__(self, integration_type_code: str) -> None:
        super().__init__(
            f"No '{integration_type_code}' connection exists for this user."
        )


class ConnectorNotAvailableError(AppError):
    """Raised when the chosen tool has no registered connector."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    error_code = "connector_not_available"

    def __init__(self, tool_code: str) -> None:
        super().__init__(
            f"Connections through '{tool_code}' are not available yet."
        )


class InvalidConnectionParametersError(AppError):
    """Raised when stored args/kwargs do not fit the connector."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "invalid_connection_parameters"


class ConnectionFailedError(ExternalServiceError):
    """Raised when the connector could not connect the user."""

    error_code = "connection_failed"


class IntegrationToolNotSelectedError(ConflictError):
    """Raised when connecting before a tool is chosen for the type."""

    error_code = "integration_tool_not_selected"

    def __init__(self, integration_type_code: str) -> None:
        super().__init__(
            f"No tool has been chosen for '{integration_type_code}' for "
            "this client yet."
        )


class IntegrationToolInactiveError(ConflictError):
    """Raised when the chosen tool has been deactivated."""

    error_code = "integration_tool_inactive"

    def __init__(self, tool_code: str) -> None:
        super().__init__(f"Integration tool '{tool_code}' is not active.")


class OperationNotFoundError(NotFoundError):
    """Raised when the tool offers no operation with this name."""

    error_code = "operation_not_found"

    def __init__(self, tool_code: str, operation: str) -> None:
        super().__init__(
            f"Tool '{tool_code}' has no operation '{operation[:64]}'."
        )


class MissingInputsError(AppError):
    """Raised when an operation needs inputs the caller did not send.

    The message lists input names such as ``kwargs.id_number``, never
    values.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "missing_inputs"

    def __init__(self, names: list[str]) -> None:
        super().__init__("Missing inputs: " + ", ".join(names) + ".")


class ToolCredentialsMissingError(ConflictError):
    """Raised when the client has not stored credentials the tool needs."""

    error_code = "tool_credentials_missing"

    def __init__(self, tool_code: str, names: list[str]) -> None:
        super().__init__(
            f"Credentials for '{tool_code}' are missing: "
            + ", ".join(names)
            + ". Ask a super admin to store them."
        )


class OperationFailedError(ExternalServiceError):
    """Raised when the provider could not be reached or answered badly."""

    error_code = "operation_failed"


class IntegrationToolDisabledError(ConflictError):
    """Raised when the client has switched the tool off."""

    error_code = "integration_tool_disabled"

    def __init__(self, tool_code: str) -> None:
        super().__init__(
            f"Integration tool '{tool_code}' is turned off for this client."
        )

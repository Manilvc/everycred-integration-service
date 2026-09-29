"""Errors raised by the clients feature."""

import uuid

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)


class ClientNotFoundError(NotFoundError):
    """Raised when no client has the requested id."""

    error_code = "client_not_found"

    def __init__(self, client_id: uuid.UUID) -> None:
        super().__init__(f"Client {client_id} does not exist.")


class ClientCodeTakenError(ConflictError):
    """Raised when creating a client with a code already in use."""

    error_code = "client_code_taken"

    def __init__(self, code: str) -> None:
        super().__init__(f"A client with code '{code}' already exists.")


class ApiKeyNotFoundError(NotFoundError):
    """Raised when the API key does not exist for the given client."""

    error_code = "api_key_not_found"

    def __init__(self, api_key_id: uuid.UUID) -> None:
        super().__init__(f"API key {api_key_id} does not exist.")


class InvalidApiKeyError(AuthenticationError):
    """Raised for any API key that cannot be used.

    Missing, malformed, unknown, revoked, and expired keys, and keys of
    inactive clients, all produce this same error.
    """

    error_code = "invalid_api_key"
    # The bearer challenge inherited from AuthenticationError does not
    # apply to API keys.
    headers = None

    def __init__(self) -> None:
        super().__init__("A valid API key is required.")


class IntegrationNotEnabledError(PermissionDeniedError):
    """Raised when the client has not enabled the integration type."""

    error_code = "integration_not_enabled"

    def __init__(self, integration_type_code: str) -> None:
        super().__init__(
            f"Integration '{integration_type_code}' is not enabled for "
            "this client."
        )


class ToolCredentialsNotFoundError(NotFoundError):
    """Raised when the client has no credentials stored for the tool."""

    error_code = "tool_credentials_not_found"

    def __init__(self, tool_code: str) -> None:
        super().__init__(
            f"No credentials are stored for tool '{tool_code}' for this "
            "client."
        )

"""Errors raised by the sessions feature."""

import uuid

from fastapi import status

from app.core.exceptions import AppError, ConflictError, NotFoundError


class SessionNotFoundError(NotFoundError):
    """Raised when the client has no session with this id."""

    error_code = "session_not_found"

    def __init__(self, session_id: uuid.UUID) -> None:
        super().__init__(f"Session {session_id} does not exist.")


class FlowNotFoundError(NotFoundError):
    """Raised when the client's tool for the type has no such flow."""

    error_code = "flow_not_found"

    def __init__(self, tool_code: str, flow: str) -> None:
        super().__init__(f"Tool '{tool_code}' has no flow '{flow[:64]}'.")


class UnexpectedInputsError(AppError):
    """Raised when inputs are sent that the flow never asks for."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "unexpected_inputs"

    def __init__(self, names: list[str]) -> None:
        super().__init__(
            "The flow does not use these inputs: " + ", ".join(names) + "."
        )


class SessionNotAwaitingInputError(ConflictError):
    """Raised when inputs arrive for a session not waiting for any."""

    error_code = "session_not_awaiting_input"

    def __init__(self, session_status: str) -> None:
        super().__init__(
            f"The session is {session_status} and does not accept inputs."
        )


class SessionNotActiveError(ConflictError):
    """Raised when a finished session is asked to change."""

    error_code = "session_not_active"

    def __init__(self, session_status: str) -> None:
        super().__init__(f"The session is already {session_status}.")


class ResultNotReadyError(ConflictError):
    """Raised when the result is requested before the session completes."""

    error_code = "result_not_ready"

    def __init__(self, session_status: str) -> None:
        super().__init__(f"The session is {session_status}; it has no result.")


class ResultExpiredError(AppError):
    """Raised when the result was deleted after the retention window."""

    status_code = status.HTTP_410_GONE
    error_code = "result_expired"

    def __init__(self) -> None:
        super().__init__(
            "The result was deleted after the retention period. Start a "
            "new session to obtain it again."
        )

"""Errors raised by the super admin feature."""

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
)


class SuperAdminAlreadyExistsError(ConflictError):
    """Raised when registering an email that already has an account."""

    error_code = "super_admin_already_exists"


class InvalidCredentialsError(AuthenticationError):
    """Raised for any failed login.

    Unknown email, wrong password, locked account, and inactive account
    all produce this same error so a caller cannot tell which emails
    are registered.
    """

    error_code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class BootstrapClosedError(PermissionDeniedError):
    """Raised when the bootstrap token is used after setup is complete."""

    error_code = "bootstrap_closed"

    def __init__(self) -> None:
        super().__init__(
            "A super admin already exists. Sign in as a super admin to "
            "register another account."
        )

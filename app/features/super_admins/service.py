"""Business rules for super admin registration and login."""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.models import utc_now
from app.core.security import (
    AccessToken,
    TokenClaims,
    burn_password_check_time,
    create_access_token,
    hash_password,
    verify_password,
)
from app.features.super_admins.exceptions import (
    InvalidCredentialsError,
    SuperAdminAlreadyExistsError,
)
from app.features.super_admins.models import SuperAdmin
from app.features.super_admins.repository import SuperAdminRepository
from app.features.super_admins.schemas import (
    SuperAdminLogin,
    SuperAdminRegistration,
)

logger = logging.getLogger(__name__)

SUPER_ADMIN_ROLE = "super_admin"


class SuperAdminService:
    """Registers super admins, logs them in, and resolves their tokens.

    Attributes:
        session: Unit of work for the current request; committed here.
        repository: Persistence for super admin rows.
        settings: Token lifetime and lockout policy.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: SuperAdminRepository,
        settings: Settings,
    ) -> None:
        self.session = session
        self.repository = repository
        self.settings = settings

    async def has_any_super_admin(self) -> bool:
        """Return True once the first super admin has been registered."""
        return await self.repository.exists_any()

    async def register_super_admin(
        self,
        registration: SuperAdminRegistration,
        registered_by: SuperAdmin | None,
    ) -> SuperAdmin:
        """Create a super admin account.

        Callers are responsible for checking that the requester may
        register accounts (see ``dependencies.authorize_registration``).

        Args:
            registration: Validated account details.
            registered_by: Super admin performing the registration, or
                None when bootstrapping the first account.

        Raises:
            SuperAdminAlreadyExistsError: The email is already taken.
        """
        if await self.repository.get_by_email(registration.email):
            raise SuperAdminAlreadyExistsError(
                "A super admin with this email already exists."
            )

        super_admin = SuperAdmin(
            email=registration.email,
            full_name=registration.full_name,
            password_hash=await hash_password(registration.password),
            created_by_id=registered_by.id if registered_by else None,
        )
        self.repository.add(super_admin)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            # Two requests for the same email can both pass the lookup
            # above; the unique index decides which one wins.
            await self.session.rollback()
            raise SuperAdminAlreadyExistsError(
                "A super admin with this email already exists."
            ) from exc

        logger.info(
            "Registered super admin %s (registered by %s)",
            super_admin.id,
            registered_by.id if registered_by else "bootstrap token",
        )
        return super_admin

    async def log_in(self, credentials: SuperAdminLogin) -> AccessToken:
        """Verify credentials and issue an access token.

        Repeated wrong passwords lock the account for
        ``login_lockout_minutes``. Every failure raises the same error,
        and takes roughly the same time, whatever the cause.

        Raises:
            InvalidCredentialsError: The login failed for any reason.
        """
        super_admin = await self.repository.get_by_email(credentials.email)
        now = utc_now()

        if super_admin is None:
            await burn_password_check_time(credentials.password)
            raise InvalidCredentialsError

        if super_admin.is_locked(now) or not super_admin.is_active:
            await burn_password_check_time(credentials.password)
            logger.warning(
                "Login refused for super admin %s (locked or inactive)",
                super_admin.id,
            )
            raise InvalidCredentialsError

        password_check = await verify_password(
            credentials.password, super_admin.password_hash
        )
        if not password_check.is_valid:
            self._record_failed_login(super_admin, now)
            await self.session.commit()
            raise InvalidCredentialsError

        super_admin.failed_login_attempts = 0
        super_admin.locked_until = None
        super_admin.last_login_at = now
        if password_check.updated_hash:
            super_admin.password_hash = password_check.updated_hash
        await self.session.commit()

        logger.info("Super admin %s logged in", super_admin.id)
        return create_access_token(
            subject=str(super_admin.id),
            role=SUPER_ADMIN_ROLE,
            settings=self.settings,
        )

    async def get_authenticated_super_admin(
        self, claims: TokenClaims
    ) -> SuperAdmin:
        """Return the active super admin a verified token belongs to.

        Looking the account up on every request means deactivating it
        takes effect immediately, not when its tokens expire.

        Raises:
            PermissionDeniedError: The token was issued for another role.
            AuthenticationError: The account no longer exists or is
                inactive.
        """
        if claims.role != SUPER_ADMIN_ROLE:
            raise PermissionDeniedError("Super admin access is required.")

        try:
            super_admin_id = uuid.UUID(claims.subject)
        except ValueError as exc:
            raise AuthenticationError("Invalid or expired token.") from exc

        super_admin = await self.repository.get_by_id(super_admin_id)
        if super_admin is None or not super_admin.is_active:
            raise AuthenticationError("Invalid or expired token.")
        return super_admin

    def _record_failed_login(
        self, super_admin: SuperAdmin, now: datetime
    ) -> None:
        super_admin.failed_login_attempts += 1
        if (
            super_admin.failed_login_attempts
            < self.settings.login_max_failed_attempts
        ):
            logger.info(
                "Wrong password for super admin %s (attempt %s)",
                super_admin.id,
                super_admin.failed_login_attempts,
            )
            return

        super_admin.locked_until = now + timedelta(
            minutes=self.settings.login_lockout_minutes
        )
        super_admin.failed_login_attempts = 0
        logger.warning(
            "Super admin %s locked until %s after repeated failed logins",
            super_admin.id,
            super_admin.locked_until.isoformat(),
        )

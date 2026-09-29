"""Database access for super admin accounts."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.super_admins.models import SuperAdmin


class SuperAdminRepository:
    """Reads and stages writes for :class:`SuperAdmin` rows.

    The repository never commits; the service that owns the unit of
    work decides when to.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, super_admin_id: uuid.UUID) -> SuperAdmin | None:
        """Return the account with this id, or None."""
        return await self.session.get(SuperAdmin, super_admin_id)

    async def get_by_email(self, email: str) -> SuperAdmin | None:
        """Return the account registered with ``email``, or None."""
        statement = select(SuperAdmin).where(SuperAdmin.email == email)
        return await self.session.scalar(statement)

    async def exists_any(self) -> bool:
        """Return True if at least one super admin account exists."""
        statement = select(SuperAdmin.id).limit(1)
        return await self.session.scalar(statement) is not None

    def add(self, super_admin: SuperAdmin) -> None:
        """Stage a new account for insertion on the next flush."""
        self.session.add(super_admin)

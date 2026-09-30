"""Database access for verification and gather sessions."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.integration_types.models import IntegrationType
from app.features.sessions.models import ACTIVE_STATUSES, IntegrationSession


class SessionRepository:
    """Queries for :class:`IntegrationSession` rows.

    Lookups are always scoped by client, so a session id guessed or
    leaked from another client resolves to "not found".
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, integration_session: IntegrationSession) -> None:
        """Stage a new session."""
        self.session.add(integration_session)

    async def get_for_client(
        self, session_id: uuid.UUID, client_id: uuid.UUID
    ) -> IntegrationSession | None:
        """Return the session if it belongs to ``client_id``."""
        return await self.session.scalar(
            select(IntegrationSession).where(
                IntegrationSession.id == session_id,
                IntegrationSession.client_id == client_id,
            )
        )

    async def claim(
        self, integration_session: IntegrationSession, from_status: str
    ) -> bool:
        """Move the session to ``processing`` if still in ``from_status``.

        A conditional UPDATE, so of two requests racing to submit inputs
        for the same session only one proceeds, without holding a row
        lock while the provider is called.
        """
        result = await self.session.execute(
            update(IntegrationSession)
            .where(
                IntegrationSession.id == integration_session.id,
                IntegrationSession.status == from_status,
            )
            .values(status="processing")
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return False
        integration_session.status = "processing"
        return True

    async def list_page(
        self,
        client_id: uuid.UUID,
        user_uuid: uuid.UUID,
        *,
        status: str | None,
        integration_type_code: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[IntegrationSession], int]:
        """Return one page of a user's sessions, newest first."""
        filters = [
            IntegrationSession.client_id == client_id,
            IntegrationSession.user_uuid == user_uuid,
        ]
        if status is not None:
            filters.append(IntegrationSession.status == status)
        if integration_type_code is not None:
            filters.append(
                IntegrationSession.integration_type.has(
                    IntegrationType.code == integration_type_code
                )
            )
        total = await self.session.scalar(
            select(func.count())
            .select_from(IntegrationSession)
            .where(*filters)
        )
        sessions = await self.session.scalars(
            select(IntegrationSession)
            .where(*filters)
            .order_by(
                IntegrationSession.created_at.desc(), IntegrationSession.id
            )
            .limit(limit)
            .offset(offset)
        )
        return sessions.all(), total or 0

    async def active_past_expiry(
        self, now: datetime, limit: int
    ) -> Sequence[IntegrationSession]:
        """Return unfinished sessions whose time is up."""
        sessions = await self.session.scalars(
            select(IntegrationSession)
            .where(
                IntegrationSession.status.in_(list(ACTIVE_STATUSES)),
                IntegrationSession.expires_at <= now,
            )
            .limit(limit)
        )
        return sessions.all()

    async def results_past_retention(
        self, now: datetime, limit: int
    ) -> Sequence[IntegrationSession]:
        """Return finished sessions whose stored result must be deleted."""
        sessions = await self.session.scalars(
            select(IntegrationSession)
            .where(
                IntegrationSession.result_secret_reference.is_not(None),
                IntegrationSession.data_expires_at <= now,
            )
            .limit(limit)
        )
        return sessions.all()

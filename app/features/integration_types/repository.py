"""Database access for integration types."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.integration_types.models import IntegrationType


class IntegrationTypeRepository:
    """Queries for :class:`IntegrationType` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> IntegrationType | None:
        """Return the integration type with this ``code``, or None."""
        statement = select(IntegrationType).where(IntegrationType.code == code)
        return await self.session.scalar(statement)

    async def get_by_codes(
        self, codes: Sequence[str]
    ) -> Sequence[IntegrationType]:
        """Return the types whose codes are in ``codes``."""
        integration_types = await self.session.scalars(
            select(IntegrationType).where(IntegrationType.code.in_(codes))
        )
        return integration_types.all()

    async def list_page(
        self, *, include_inactive: bool, limit: int, offset: int
    ) -> tuple[Sequence[IntegrationType], int]:
        """Return one page of integration types and the total count.

        Results are ordered by ``display_order`` and then ``name``, with
        ``id`` as a final tie-breaker so pages never overlap or skip
        rows when two types share the same order and name.

        Args:
            include_inactive: Also return types with ``is_active`` off.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            The rows on this page and the number of matching rows
            across all pages.
        """
        filters = [] if include_inactive else [IntegrationType.is_active]

        total_statement = (
            select(func.count()).select_from(IntegrationType).where(*filters)
        )
        page_statement = (
            select(IntegrationType)
            .where(*filters)
            .order_by(
                IntegrationType.display_order,
                IntegrationType.name,
                IntegrationType.id,
            )
            .limit(limit)
            .offset(offset)
        )

        total = await self.session.scalar(total_statement) or 0
        integration_types = (await self.session.scalars(page_statement)).all()
        return integration_types, total

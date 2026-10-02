"""Database access for integration tools."""

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.integration_tools.models import (
    IntegrationTool,
    IntegrationToolField,
)
from app.features.integration_types.models import IntegrationType


class IntegrationToolRepository:
    """Queries for :class:`IntegrationTool` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> IntegrationTool | None:
        """Return the tool with this code, or None."""
        return await self.session.scalar(
            select(IntegrationTool).where(IntegrationTool.code == code)
        )

    async def list_active_for_types(
        self, integration_type_ids: Collection[uuid.UUID]
    ) -> Sequence[IntegrationTool]:
        """Return active tools serving any of the types, in display order."""
        if not integration_type_ids:
            return []
        tools = await self.session.scalars(
            select(IntegrationTool)
            .where(
                IntegrationTool.is_active,
                IntegrationTool.integration_types.any(
                    IntegrationType.id.in_(list(integration_type_ids))
                ),
            )
            .order_by(
                IntegrationTool.display_order,
                IntegrationTool.name,
                IntegrationTool.id,
            )
        )
        return tools.all()

    def add(self, tool: IntegrationTool) -> None:
        """Stage a new tool for insertion."""
        self.session.add(tool)

    async def list_page(
        self,
        *,
        limit: int,
        offset: int,
        integration_type_code: str | None = None,
        integration_type_ids: Collection[uuid.UUID] | None = None,
        include_inactive: bool = False,
    ) -> tuple[Sequence[IntegrationTool], int]:
        """Return one page of tools and the total number that match.

        Args:
            limit: Maximum number of tools to return.
            offset: Number of tools to skip.
            integration_type_code: Keep tools serving this type.
            integration_type_ids: Keep tools serving at least one of
                these types. An empty collection matches nothing.
            include_inactive: Also return tools with ``is_active`` off.
        """
        filters = []
        if not include_inactive:
            filters.append(IntegrationTool.is_active)
        if integration_type_code is not None:
            filters.append(
                IntegrationTool.integration_types.any(
                    IntegrationType.code == integration_type_code
                )
            )
        if integration_type_ids is not None:
            filters.append(
                IntegrationTool.integration_types.any(
                    IntegrationType.id.in_(list(integration_type_ids))
                )
            )

        total = await self.session.scalar(
            select(func.count()).select_from(IntegrationTool).where(*filters)
        )
        tools = await self.session.scalars(
            select(IntegrationTool)
            .where(*filters)
            .order_by(
                IntegrationTool.display_order,
                IntegrationTool.name,
                IntegrationTool.id,
            )
            .limit(limit)
            .offset(offset)
        )
        return tools.all(), total or 0


class IntegrationToolFieldRepository:
    """Queries for :class:`IntegrationToolField` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_page(
        self,
        integration_tool_id: uuid.UUID,
        *,
        flow: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[IntegrationToolField], int]:
        """Return one page of a tool's fields, by flow then key."""
        filters = [
            IntegrationToolField.integration_tool_id == integration_tool_id
        ]
        if flow is not None:
            filters.append(IntegrationToolField.flow == flow)
        total = await self.session.scalar(
            select(func.count())
            .select_from(IntegrationToolField)
            .where(*filters)
        )
        fields = await self.session.scalars(
            select(IntegrationToolField)
            .where(*filters)
            .order_by(IntegrationToolField.flow, IntegrationToolField.key)
            .limit(limit)
            .offset(offset)
        )
        return fields.all(), total or 0

    async def record(
        self,
        integration_tool_id: uuid.UUID,
        flow: str,
        types_by_key: dict[str, str],
        seen_at: datetime,
    ) -> int:
        """Stage seen fields: new keys are added, known ones refreshed.

        Returns:
            How many keys were new.
        """
        if not types_by_key:
            return 0
        known = {
            field.key: field
            for field in await self.session.scalars(
                select(IntegrationToolField).where(
                    IntegrationToolField.integration_tool_id
                    == integration_tool_id,
                    IntegrationToolField.flow == flow,
                    IntegrationToolField.key.in_(list(types_by_key)),
                )
            )
        }
        for key, value_type in types_by_key.items():
            field = known.get(key)
            if field is None:
                self.session.add(
                    IntegrationToolField(
                        integration_tool_id=integration_tool_id,
                        flow=flow,
                        key=key,
                        value_type=value_type,
                        last_seen_at=seen_at,
                    )
                )
            else:
                field.value_type = value_type
                field.last_seen_at = seen_at
        return len(types_by_key) - len(known)

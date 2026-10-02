"""Database access for integration tools."""

import uuid
from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import false, func, or_, select
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
        client_ids: Collection[uuid.UUID | None],
        flow: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[IntegrationToolField], int]:
        """Return one page of a tool's fields in the given scopes.

        Args:
            integration_tool_id: Tool whose fields to return.
            client_ids: Scopes to include; ``None`` stands for the
                global fields, a client id for that client's own.
            flow: Keep only fields of this flow.
            limit: Maximum number of fields to return.
            offset: Number of fields to skip.
        """
        filters = [
            IntegrationToolField.integration_tool_id == integration_tool_id,
            self._in_scopes(client_ids),
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
            .order_by(
                IntegrationToolField.flow,
                IntegrationToolField.key,
                # Global before client fields for the same key.
                IntegrationToolField.client_id.is_not(None),
            )
            .limit(limit)
            .offset(offset)
        )
        return fields.all(), total or 0

    async def replace(
        self,
        integration_tool_id: uuid.UUID,
        client_id: uuid.UUID | None,
        fields: Sequence[IntegrationToolField],
    ) -> None:
        """Make ``fields`` the whole list for one scope of a tool.

        A field already in the list (same flow and key) is updated in
        place, so its id never changes and stays usable as a reference;
        new ones are added and missing ones deleted. Staged only; the
        caller commits, so the list changes atomically.
        """
        existing = await self.session.scalars(
            select(IntegrationToolField).where(
                IntegrationToolField.integration_tool_id
                == integration_tool_id,
                self._in_scopes([client_id]),
            )
        )
        current = {(field.flow, field.key): field for field in existing}
        wanted = {(field.flow, field.key) for field in fields}
        for key, stale in current.items():
            if key not in wanted:
                await self.session.delete(stale)
        for field in fields:
            kept = current.get((field.flow, field.key))
            if kept is None:
                field.integration_tool_id = integration_tool_id
                field.client_id = client_id
                self.session.add(field)
            else:
                kept.label = field.label
                kept.value_type = field.value_type

    @staticmethod
    def _in_scopes(client_ids: Collection[uuid.UUID | None]) -> Any:
        ids = [client_id for client_id in client_ids if client_id]
        conditions = []
        if None in client_ids:
            conditions.append(IntegrationToolField.client_id.is_(None))
        if ids:
            conditions.append(IntegrationToolField.client_id.in_(ids))
        return or_(*conditions) if conditions else false()

"""Request and response models for user integration connections."""

import json
import keyword
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.integration_tools.schemas import IntegrationToolSummary
from app.features.user_connections.models import ConnectionStatus

MAX_ARGS = 20
MAX_KWARGS = 50
MAX_PARAMETERS_BYTES = 16_384


class ConnectionParameters(BaseModel):
    """Arguments passed to the integration's connector.

    The connector is called as ``connect(*args, **kwargs)``. Values may
    be secrets; they are encrypted before storage and never returned.
    """

    model_config = ConfigDict(extra="forbid")

    args: list[Any] = Field(default_factory=list, max_length=MAX_ARGS)
    kwargs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_parameters(self) -> "ConnectionParameters":
        """Reject kwargs that cannot be passed as Python keyword arguments.

        Names must be identifiers so ``**kwargs`` works, must not be
        keywords, and must not start with an underscore, which is kept
        for connector internals.
        """
        if len(self.kwargs) > MAX_KWARGS:
            raise ValueError(f"at most {MAX_KWARGS} kwargs are allowed")
        for name in self.kwargs:
            if (
                not name.isidentifier()
                or keyword.iskeyword(name)
                or name.startswith("_")
            ):
                raise ValueError(
                    f"kwarg name '{name[:40]}' must be a Python identifier "
                    "that is not a keyword and does not start with '_'"
                )
        encoded_size = len(json.dumps(self.model_dump()).encode())
        if encoded_size > MAX_PARAMETERS_BYTES:
            raise ValueError(
                f"parameters must be at most {MAX_PARAMETERS_BYTES} bytes"
            )
        return self


class ParameterSummary(BaseModel):
    """Shape of the stored parameters, without their values."""

    arg_count: int
    kwarg_names: list[str]


class UserConnectionResponse(BaseModel):
    """A user's connection to one integration type."""

    id: uuid.UUID
    client_id: uuid.UUID
    user_uuid: uuid.UUID
    integration_type_code: str
    integration_type_name: str
    integration_tool: IntegrationToolSummary | None = Field(
        description="Tool the client currently uses for this type.",
    )
    status: ConnectionStatus
    parameters: ParameterSummary
    connection_details: dict[str, Any]
    last_error: str | None
    last_attempt_at: datetime | None
    last_connected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OperationRequest(ConnectionParameters):
    """Inputs for one operation call.

    ``kwargs`` are merged over the user's saved kwargs, so values saved
    once (for example a consent flag) need not be resent. ``args``, when
    given, replace the saved args.
    """


class OperationResultResponse(BaseModel):
    """Normalised result of an operation, whatever the provider.

    Attributes:
        operation: Operation that ran.
        integration_type_code: Type it ran under.
        tool_code: Tool that served it.
        success: Whether the provider reported success. A provider
            saying "no" (an invalid document, say) is ``false`` with
            HTTP 200; transport failures are errors instead.
        provider_status_code: HTTP status returned by the provider.
        data: The provider's result payload.
        message: The provider's message, if any.
    """

    operation: str
    integration_type_code: str
    tool_code: str
    success: bool
    provider_status_code: int | None
    data: Any
    message: str | None

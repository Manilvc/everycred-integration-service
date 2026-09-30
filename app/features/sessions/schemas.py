"""Request and response models for verification and gather sessions."""

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.http.config import OPERATION_NAME_PATTERN
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.features.sessions.models import (
    SessionOutcome,
    SessionPurpose,
    SessionStatus,
)
from app.shared.pagination import PaginationParams

INPUT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_INPUTS = 30
MAX_INPUTS_BYTES = 16_384


def _check_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    if len(inputs) > MAX_INPUTS:
        raise ValueError(f"at most {MAX_INPUTS} inputs are allowed")
    for name in inputs:
        if not re.fullmatch(INPUT_NAME_PATTERN, name):
            raise ValueError(
                f"input name '{name[:40]}' must be lower_snake_case"
            )
    if len(json.dumps(inputs).encode()) > MAX_INPUTS_BYTES:
        raise ValueError(f"inputs must be at most {MAX_INPUTS_BYTES} bytes")
    return inputs


class SessionCreate(BaseModel):
    """Start a verification or gather flow for one user.

    ``inputs`` may already hold everything the flow needs; anything a
    later step asks for (an OTP, say) is sent afterwards to the inputs
    endpoint. Values are kept only while the session is active.
    """

    model_config = ConfigDict(extra="forbid")

    integration_type: str = Field(
        min_length=1, max_length=CODE_MAX_LENGTH, examples=["confirm"]
    )
    flow: str = Field(pattern=OPERATION_NAME_PATTERN, examples=["aadhaar_otp"])
    reference: str | None = Field(
        default=None,
        max_length=128,
        description="Your own reference, e.g. the invite id; echoed back.",
    )
    inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def check_inputs(cls, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate names and bound the size; values are never echoed."""
        return _check_inputs(inputs)


class SessionInputs(BaseModel):
    """Inputs the session is waiting for, such as an OTP."""

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, Any] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def check_inputs(cls, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate names and bound the size; values are never echoed."""
        return _check_inputs(inputs)


class SessionFilters(PaginationParams):
    """Query parameters for listing a user's sessions."""

    status: SessionStatus | None = None
    integration_type: str | None = Field(
        default=None, max_length=CODE_MAX_LENGTH
    )


SessionQuery = Annotated[SessionFilters, Query()]


class SessionResponse(BaseModel):
    """A session's state. Never contains inputs or attributes.

    Attributes:
        awaiting_inputs: Input names to send next, when
            ``status`` is ``awaiting_input``.
        result_attributes: Names of the attributes available from the
            result endpoint until ``data_expires_at``.
    """

    id: uuid.UUID
    user_uuid: uuid.UUID
    integration_type: str
    tool_code: str
    flow: str
    purpose: SessionPurpose
    status: SessionStatus
    outcome: SessionOutcome | None
    awaiting_inputs: list[str]
    reference: str | None
    failure_code: str | None
    failure_message: str | None
    result_attributes: list[str]
    expires_at: datetime
    completed_at: datetime | None
    data_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionResultResponse(BaseModel):
    """The attributes a completed session returned.

    For verification, ``attributes`` are the verified values (empty when
    not verified). For gather, they are the fetched values, keyed by the
    attribute names the flow maps them to.
    """

    session_id: uuid.UUID
    outcome: SessionOutcome
    attributes: dict[str, Any]
    data_expires_at: datetime | None

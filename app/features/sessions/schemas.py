"""Request and response models for verification and gather sessions."""

import json
import re
import uuid
from datetime import UTC, datetime
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


class VerifiedUserFilters(PaginationParams):
    """Query parameters for listing users whose KYC succeeded."""

    integration_type: str | None = Field(
        default=None,
        max_length=CODE_MAX_LENGTH,
        description="Only verifications of this type, e.g. `confirm`.",
    )
    flow: str | None = Field(
        default=None,
        pattern=OPERATION_NAME_PATTERN,
        description="Only verifications by this flow, e.g. `aadhaar_otp`.",
    )
    verified_after: datetime | None = Field(
        default=None,
        description="Only users verified at or after this time (ISO 8601).",
    )
    user_uuid: uuid.UUID | None = Field(
        default=None, description="Check a single user."
    )

    @field_validator("verified_after")
    @classmethod
    def assume_utc(cls, value: datetime | None) -> datetime | None:
        """Read a time without a timezone as UTC."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


SessionQuery = Annotated[SessionFilters, Query()]
VerifiedUserQuery = Annotated[VerifiedUserFilters, Query()]


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


class VerifiedUserResponse(BaseModel):
    """A user whose identity verification succeeded, latest first.

    Attributes:
        user_uuid: The client's id for the user.
        verified_at: When the user's most recent successful
            verification completed.
        session_id: That session, for its result or details.
        integration_type: Its integration type, e.g. ``confirm``.
        tool_code: The tool that verified the user, e.g. ``surepass``.
        flow: The flow used, e.g. ``aadhaar_otp``.
    """

    user_uuid: uuid.UUID
    verified_at: datetime
    session_id: uuid.UUID
    integration_type: str
    tool_code: str
    flow: str

"""Request and response models for verification and gather sessions."""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Query
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.connectors.http.config import OPERATION_NAME_PATTERN
from app.features.integration_tools.models import (
    TOOL_CODE_MAX_LENGTH,
    FieldValueType,
)
from app.features.integration_types.models import CODE_MAX_LENGTH
from app.features.sessions.models import (
    CONSENT_NOTICE_VERSION_MAX_LENGTH,
    CONSENT_PURPOSE_MAX_LENGTH,
    CONSENT_REFERENCE_MAX_LENGTH,
    SessionOutcome,
    SessionPurpose,
    SessionStatus,
)
from app.shared.pagination import PaginationParams

INPUT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_INPUTS = 30
MAX_INPUTS_BYTES = 16_384
MAX_REQUESTED_FIELDS = 100
# BCP 47 tags as used for notices, e.g. "en", "hi", "en-IN".
LANGUAGE_TAG_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$"


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


class SessionConsent(BaseModel):
    """The holder's consent, as the client recorded it.

    Under the DPDP Act 2023 the client (the data fiduciary) must show
    the holder a notice and obtain consent before their data is
    processed. The service keeps this record with the session as
    evidence; it is never sent to the provider.

    Attributes:
        purpose: The specific purpose shown to the holder, e.g.
            "Verify your identity to receive your employee credential".
        notice_version: Version of the notice text the holder accepted.
        language: Language the notice was shown in.
        consented_at: When the holder gave consent. Must be recent.
        reference: The client's own id for its consent record.
    """

    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=CONSENT_PURPOSE_MAX_LENGTH)
    notice_version: str = Field(
        min_length=1, max_length=CONSENT_NOTICE_VERSION_MAX_LENGTH
    )
    language: str = Field(default="en", pattern=LANGUAGE_TAG_PATTERN)
    consented_at: AwareDatetime
    reference: str | None = Field(
        default=None, max_length=CONSENT_REFERENCE_MAX_LENGTH
    )

    @field_validator("purpose")
    @classmethod
    def strip_purpose(cls, purpose: str) -> str:
        """Reject a purpose that is only whitespace."""
        stripped = purpose.strip()
        if not stripped:
            raise ValueError("purpose must not be blank")
        return stripped


class SessionCreate(BaseModel):
    """Start a verification or gather flow for one user.

    ``inputs`` may already hold everything the flow needs; anything a
    later step asks for (an OTP, say) is sent afterwards to the inputs
    endpoint. Values are kept only while the session is active.

    ``requested_attributes`` and ``requested_fields`` limit what is
    kept to what the client needs (data minimisation). When either is
    given, only the listed outputs and fields are stored; when neither
    is, every output of the flow is.
    """

    model_config = ConfigDict(extra="forbid")

    integration_type: str = Field(
        min_length=1, max_length=CODE_MAX_LENGTH, examples=["confirm"]
    )
    tool_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=TOOL_CODE_MAX_LENGTH,
        examples=["surepass"],
        description=(
            "Tool to run the flow through, when the type has several. "
            "The type's default tool when omitted."
        ),
    )
    flow: str = Field(pattern=OPERATION_NAME_PATTERN, examples=["aadhaar_otp"])
    reference: str | None = Field(
        default=None,
        max_length=128,
        description="Your own reference, e.g. the invite id; echoed back.",
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
    consent: SessionConsent
    redirect_uri: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Where the provider sends the holder back, for flows that "
            "sign in with the provider. Must be listed in the client's "
            "`redirect_uris` setting for the tool or the type."
        ),
    )
    requested_attributes: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Flow outputs to keep; all of them when omitted.",
    )
    requested_fields: list[uuid.UUID] | None = Field(
        default=None,
        max_length=MAX_REQUESTED_FIELDS,
        description=(
            "Ids of the tool's field keys to read from the provider's "
            "response, so the result can be matched to your own fields."
        ),
    )

    @field_validator("inputs")
    @classmethod
    def check_inputs(cls, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate names and bound the size; values are never echoed."""
        return _check_inputs(inputs)


class SessionAuthorizationCallback(BaseModel):
    """What the provider sent back to the client's redirect URI.

    Relay ``state`` and either ``code`` or ``error`` exactly as
    received. ``state`` identifies the session and works only once.
    """

    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=16, max_length=200)
    code: str | None = Field(default=None, min_length=1, max_length=4096)
    error: str | None = Field(default=None, max_length=100)


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


class SessionNextAction(BaseModel):
    """Something the holder must do outside the API.

    Attributes:
        type: ``redirect``: open ``url`` in the holder's browser.
        url: Provider sign-in page. Single-use; it is only returned
            when the session is started, never when polled.
    """

    type: Literal["redirect"]
    url: str


class SessionConsentResponse(BaseModel):
    """The consent recorded for a session."""

    purpose: str
    notice_version: str
    language: str
    consented_at: datetime
    reference: str | None
    withdrawn_at: datetime | None


class SessionResponse(BaseModel):
    """A session's state. Never contains inputs or attributes.

    Attributes:
        awaiting_inputs: Input names to send next, when
            ``status`` is ``awaiting_input``.
        next_action: Set when ``status`` is ``awaiting_redirect``, in
            the response that started the session only.
        result_attributes: Names of the attributes available from the
            result endpoint until ``data_expires_at``.
        result_fields: Ids of the requested fields the result holds.
        consent: Absent only for sessions created before consent was
            recorded.
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
    next_action: SessionNextAction | None = None
    reference: str | None
    failure_code: str | None
    failure_message: str | None
    result_attributes: list[str]
    result_fields: list[uuid.UUID]
    consent: SessionConsentResponse | None
    expires_at: datetime
    completed_at: datetime | None
    data_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionResultField(BaseModel):
    """The value of one requested tool field.

    Attributes:
        field_id: The tool field's id, as sent in ``requested_fields``.
        key: Dot path the value was read from.
    """

    field_id: uuid.UUID
    key: str
    value: Any


class SessionResultResponse(BaseModel):
    """The attributes a completed session returned.

    For verification, ``attributes`` are the verified values (empty when
    not verified). For gather, they are the fetched values, keyed by the
    attribute names the flow maps them to. ``fields`` holds the
    requested tool fields the provider's response contained.
    """

    session_id: uuid.UUID
    outcome: SessionOutcome
    attributes: dict[str, Any]
    fields: list[SessionResultField]
    data_expires_at: datetime | None


class FlowInputResponse(BaseModel):
    """One input a holder-facing screen should ask for.

    Without an ``input_specs`` entry in the tool configuration, the
    label is the input name and the type is ``text``.
    """

    name: str
    label: str
    input_type: str
    pattern: str | None
    min_length: int | None
    max_length: int | None
    is_masked: bool
    hint: str | None


class FlowFieldResponse(BaseModel):
    """A field key a flow's response holds, to request by id."""

    id: uuid.UUID
    key: str
    label: str | None
    value_type: FieldValueType


class FlowDescriptorResponse(BaseModel):
    """A flow described for building a holder-facing screen.

    Attributes:
        kind: ``input`` when the holder types values (an OTP, say);
            ``redirect`` when they sign in with the provider.
        inputs: Inputs in the order the flow asks for them.
        outputs: Attribute names a completed session can return.
        fields: Field keys the client can request with
            ``requested_fields``.
    """

    name: str
    purpose: SessionPurpose
    kind: Literal["input", "redirect"]
    description: str | None
    inputs: list[FlowInputResponse]
    outputs: list[str]
    fields: list[FlowFieldResponse]


class FlowCatalogResponse(BaseModel):
    """The flows the client's chosen tool offers for a type."""

    integration_type: str
    tool_code: str
    tool_name: str
    flows: list[FlowDescriptorResponse]


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

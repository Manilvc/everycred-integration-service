"""Run verification and gather flows as resumable sessions.

A session runs the steps of one flow from the tool's configuration. It
runs as far as it can, pausing when a step needs an input the holder has
not given yet (an OTP sent by the previous step, typically), and resumes
when the client submits it. When the last step finishes, the flow's
outputs are read from the response and kept, encrypted, until the
retention window ends.

A flow may open with a sign-in at the provider (OpenID Connect): the
session then waits as ``awaiting_redirect`` until the client relays the
provider's callback, and the ID token's claims feed the rest of the
flow.

Nothing personal is stored in the database: inputs and captured values
live in the secret store while the session is active, and the result
only until ``data_expires_at``, or until the client erases it or the
holder withdraws consent. Webhook events carry the session's status,
never its data.
"""

import asyncio
import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    ConnectorError,
    InvalidInputError,
    MissingParametersError,
    OperationOutcome,
    UnknownOperationError,
)
from app.connectors.http import authorization
from app.connectors.http.config import (
    FlowCondition,
    FlowConfig,
    OAuth2AuthorizationCode,
)
from app.core.config import Settings
from app.core.context import get_request_id
from app.core.data_paths import MISSING, read_path
from app.core.exceptions import AppError
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.clients.models import Client
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
)
from app.features.sessions import policies
from app.features.sessions.exceptions import (
    AuthorizationNotFoundError,
    FlowNotFoundError,
    ResultExpiredError,
    ResultNotReadyError,
    SessionNotActiveError,
    SessionNotAwaitingInputError,
    SessionNotFoundError,
    UnexpectedInputsError,
    UnknownRequestedFieldsError,
)
from app.features.sessions.models import (
    FAILURE_MESSAGE_MAX_LENGTH,
    IntegrationSession,
    SessionOutcome,
    SessionPurpose,
    SessionStatus,
)
from app.features.sessions.repository import SessionRepository
from app.features.sessions.schemas import (
    FlowCatalogResponse,
    FlowDescriptorResponse,
    FlowFieldResponse,
    FlowInputResponse,
    SessionAuthorizationCallback,
    SessionConsentResponse,
    SessionCreate,
    SessionFilters,
    SessionInputs,
    SessionNextAction,
    SessionResponse,
    SessionResultField,
    SessionResultResponse,
    VerifiedUserFilters,
    VerifiedUserResponse,
)
from app.features.user_connections.models import IntegrationOperationLog
from app.features.user_connections.targets import (
    IntegrationTarget,
    IntegrationTargetResolver,
    missing_inputs_error,
)
from app.features.webhooks.service import WebhookService
from app.shared.schemas import Page

logger = logging.getLogger(__name__)

SESSION_PREFIX = "session."
PROVIDER_REJECTED = "provider_rejected"
# Recorded in integration_operation_logs like a provider operation.
CODE_EXCHANGE_OPERATION = "authorization_code_exchange"
# Webhook events, named after the status they report.
FINAL_EVENTS = {
    SessionStatus.COMPLETED: "session.completed",
    SessionStatus.FAILED: "session.failed",
    SessionStatus.EXPIRED: "session.expired",
}


class StepFailedError(Exception):
    """A step could not produce an answer; the session fails.

    Attributes:
        code: Stored as ``failure_code``.
        message: Stored as ``failure_message``; safe to show the client.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:FAILURE_MESSAGE_MAX_LENGTH]


@dataclass(slots=True)
class FlowState:
    """What a session carries between steps, kept in the secret store.

    Attributes:
        inputs: Everything the holder has supplied so far.
        captured: Values earlier steps captured from responses.
        pending_authorization: The PKCE verifier, nonce, and redirect
            URI of a sign-in in progress; dropped once it completes.
    """

    inputs: dict[str, Any] = field(default_factory=dict)
    captured: dict[str, Any] = field(default_factory=dict)
    pending_authorization: dict[str, str] | None = None

    def to_secret(self) -> dict[str, Any]:
        """Serialise for the secret store."""
        return {
            "inputs": self.inputs,
            "captured": self.captured,
            "pending_authorization": self.pending_authorization,
        }

    @classmethod
    def from_secret(cls, value: dict[str, Any]) -> "FlowState":
        """Rebuild from what :meth:`to_secret` stored."""
        return cls(
            inputs=dict(value.get("inputs", {})),
            captured=dict(value.get("captured", {})),
            pending_authorization=value.get("pending_authorization"),
        )


def state_secret_name(client_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Secret store name for a session's inputs while it is active."""
    return f"clients/{client_id}/sessions/{session_id}/state"


def result_secret_name(client_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Secret store name for a session's result attributes."""
    return f"clients/{client_id}/sessions/{session_id}/result"


def condition_holds(data: Any, condition: FlowCondition) -> bool:
    """Whether ``data`` satisfies one ``verified_when`` condition."""
    value = read_path(data, condition.path)
    if value is MISSING:
        return False
    if condition.equals is not None:
        return value == condition.equals
    if condition.one_of is not None:
        return value in condition.one_of
    return bool(value)


def map_outputs(
    outputs: dict[str, str], data: Any, captured: dict[str, Any]
) -> dict[str, Any]:
    """Read each output attribute from the final data or captured values.

    Attributes whose path is absent in the response are left out rather
    than returned as null, so "the provider did not say" and "the
    provider said null" stay distinguishable.
    """
    attributes: dict[str, Any] = {}
    for attribute, path in outputs.items():
        if path.startswith(SESSION_PREFIX):
            value = captured.get(path.removeprefix(SESSION_PREFIX), MISSING)
        else:
            value = read_path(data, path)
        if value is not MISSING:
            attributes[attribute] = value
    return attributes


class SessionService:
    """Starts, resumes, and reports on verification and gather sessions.

    Attributes:
        session: Unit of work; committed here.
        sessions: Persistence for sessions.
        targets: Resolves the client's tool for a type.
        tool_fields: Field keys a client may request by id.
        secret_store: Holds session inputs and results.
        webhooks: Queues status events for the client.
        settings: Timeouts, session lifetime, and retention.
    """

    def __init__(
        self,
        session: AsyncSession,
        sessions: SessionRepository,
        targets: IntegrationTargetResolver,
        tool_fields: IntegrationToolFieldRepository,
        secret_store: SecretStore,
        webhooks: WebhookService,
        settings: Settings,
    ) -> None:
        self.session = session
        self.sessions = sessions
        self.targets = targets
        self.tool_fields = tool_fields
        self.secret_store = secret_store
        self.webhooks = webhooks
        self.settings = settings

    async def describe_flows(
        self,
        client: Client,
        integration_type: str,
        tool_code: str | None = None,
    ) -> FlowCatalogResponse:
        """Describe the flows of the client's tool for a type.

        Everything a holder-facing screen needs to ask for the inputs,
        and the field keys the client can request by id.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: Not enabled for the client.
            IntegrationToolNotSelectedError: No tool named, and no
                default chosen for the type.
            IntegrationToolNotFoundError: ``tool_code`` does not serve
                the type.
            IntegrationToolInactiveError: The chosen tool is inactive.
            IntegrationToolDisabledError: The client switched it off.
            ConnectorNotAvailableError: The tool has no connector.
        """
        target = await self.targets.resolve(
            client, integration_type, tool_code
        )
        flows = target.connector_class.describe_flows(
            target.tool.connector_config
        )
        fields_by_flow: dict[str, list[FlowFieldResponse]] = {}
        for tool_field in await self.tool_fields.list_visible_to_client(
            target.tool.id, client.id
        ):
            fields_by_flow.setdefault(tool_field.flow, []).append(
                FlowFieldResponse(
                    id=tool_field.id,
                    key=tool_field.key,
                    label=tool_field.label,
                    value_type=tool_field.value_type,
                )
            )
        return FlowCatalogResponse(
            integration_type=target.integration_type.code,
            tool_code=target.tool.code,
            tool_name=target.tool.name,
            flows=[
                FlowDescriptorResponse(
                    name=name,
                    purpose=SessionPurpose(flow.purpose),
                    kind=flow.kind,
                    description=flow.description,
                    inputs=[
                        self._describe_input(flow, input_name)
                        for input_name in flow.all_inputs()
                    ],
                    outputs=list(flow.outputs),
                    fields=fields_by_flow.get(name, []),
                )
                for name, flow in flows.items()
            ],
        )

    async def start(
        self, client: Client, user_uuid: uuid.UUID, request: SessionCreate
    ) -> SessionResponse:
        """Create a session and run it until it needs input or finishes.

        Problems with the setup (type not enabled, unknown flow) and
        with the request (stale consent, malformed inputs) are raised
        before anything is stored. Problems with the provider are
        recorded on the session, which is returned as ``failed``.

        A flow that opens with a sign-in returns ``awaiting_redirect``
        with ``next_action.url`` for the holder's browser.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: Not enabled for the client.
            IntegrationToolNotSelectedError: No tool named, and no
                default chosen for the type.
            IntegrationToolNotFoundError: ``tool_code`` does not serve
                the type.
            IntegrationToolInactiveError: The chosen tool is inactive.
            IntegrationToolDisabledError: The client switched it off.
            ConnectorNotAvailableError: The tool has no connector.
            FlowNotFoundError: The tool has no such flow.
            UnexpectedInputsError: Inputs the flow never uses were sent.
            InputValidationError: Inputs break the flow's input specs.
            ConsentNotRecentError: The consent is stale or in the future.
            InvalidRedirectUriError: The redirect URI is not acceptable.
            UnknownRequestedAttributesError: Unknown requested outputs.
            UnknownRequestedFieldsError: Unknown requested fields.
        """
        target = await self.targets.resolve(
            client, request.integration_type, request.tool_code
        )
        flow = self._flow_or_raise(target, request.flow)
        self._reject_unexpected(flow, request.inputs)
        policies.check_input_formats(flow, request.inputs)
        now = utc_now()
        policies.check_consent_recent(request.consent, now)
        redirect_uri = policies.check_redirect_uri(
            flow, request.redirect_uri, target.settings
        )
        policies.check_requested_attributes(flow, request.requested_attributes)
        requested_fields = await self._requested_fields(
            client, target, request
        )

        integration_session = IntegrationSession(
            id=uuid.uuid4(),
            client_id=client.id,
            user_uuid=user_uuid,
            integration_type_id=target.integration_type.id,
            integration_tool_id=target.tool.id,
            flow=request.flow,
            purpose=flow.purpose,
            status=SessionStatus.PROCESSING,
            current_step=0,
            awaiting_inputs=[],
            result_attributes=[],
            result_fields=[],
            requested_attributes=request.requested_attributes,
            requested_fields=requested_fields,
            reference=request.reference,
            consent_purpose=request.consent.purpose,
            consent_notice_version=request.consent.notice_version,
            consent_language=request.consent.language,
            consented_at=request.consent.consented_at,
            consent_reference=request.consent.reference,
            expires_at=now
            + timedelta(minutes=self.settings.session_ttl_minutes),
        )
        integration_session.integration_type = target.integration_type
        integration_session.integration_tool = target.tool
        self.sessions.add(integration_session)
        await self.session.flush()

        logger.info(
            "Session %s started: %s/%s flow %s for user %s of client %s",
            integration_session.id,
            target.integration_type.code,
            target.tool.code,
            request.flow,
            user_uuid,
            client.id,
        )
        state = FlowState(inputs=dict(request.inputs))
        next_action: SessionNextAction | None = None
        if flow.authorization is not None and redirect_uri is not None:
            next_action = await self._begin_authorization(
                integration_session,
                client,
                target,
                flow.authorization,
                state,
                redirect_uri,
            )
        else:
            await self._advance(
                integration_session, client, target, flow, state
            )
        await self.session.commit()
        return self._to_response(integration_session, next_action)

    async def complete_authorization(
        self, client: Client, callback: SessionAuthorizationCallback
    ) -> SessionResponse:
        """Finish a provider sign-in and run the rest of the flow.

        ``state`` is single-use: it is cleared before the code is
        exchanged, so a replayed callback finds no session.

        Raises:
            AuthorizationNotFoundError: No session of the client waits
                for this state (unknown, already used, or finished).
        """
        integration_session = await self.sessions.get_by_oauth_state(
            client.id, authorization.hash_state(callback.state)
        )
        if integration_session is None:
            raise AuthorizationNotFoundError
        if await self._expire_if_due(integration_session):
            await self.session.commit()
            raise AuthorizationNotFoundError
        if integration_session.status != SessionStatus.AWAITING_REDIRECT:
            raise AuthorizationNotFoundError

        try:
            target, flow = await self._current_target(
                client, integration_session
            )
        except StepFailedError as failure:
            await self._fail(integration_session, failure)
            await self.session.commit()
            return self._to_response(integration_session)
        if not await self.sessions.claim(
            integration_session, SessionStatus.AWAITING_REDIRECT
        ):
            raise AuthorizationNotFoundError
        integration_session.oauth_state_hash = None
        await self.session.commit()

        try:
            state = FlowState.from_secret(
                await self.secret_store.read(
                    integration_session.state_secret_reference or ""
                )
            )
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "session_state_unavailable",
                    "The session's sign-in details could not be read.",
                ),
            )
            await self.session.commit()
            return self._to_response(integration_session)

        pending = state.pending_authorization
        if (
            callback.error
            or not callback.code
            or pending is None
            or flow.authorization is None
        ):
            await self._fail(
                integration_session,
                StepFailedError(
                    "authorization_declined",
                    "The holder did not complete sign-in with the provider.",
                ),
            )
            await self.session.commit()
            return self._to_response(integration_session)

        sign_in = await self._exchange_code(
            integration_session,
            client,
            target,
            flow.authorization,
            callback.code,
            pending,
        )
        if sign_in is not None:
            state.pending_authorization = None
            await self._advance(
                integration_session,
                client,
                target,
                flow,
                state,
                initial_data=sign_in.claims,
                user_access_token=sign_in.access_token,
            )
        await self.session.commit()
        return self._to_response(integration_session)

    async def submit_inputs(
        self, client: Client, session_id: uuid.UUID, request: SessionInputs
    ) -> SessionResponse:
        """Add inputs to a waiting session and continue running it.

        Raises:
            SessionNotFoundError: The client has no such session.
            SessionNotActiveError: The session has finished or expired.
            SessionNotAwaitingInputError: It is busy processing, or
                waiting for a sign-in rather than inputs.
            UnexpectedInputsError: Inputs the flow never uses were sent.
            InputValidationError: Inputs break the flow's input specs.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if await self._expire_if_due(integration_session):
            # Kept, with its webhook event, although the request fails.
            await self.session.commit()
        if not integration_session.is_active:
            raise SessionNotActiveError(integration_session.status)
        if integration_session.status != SessionStatus.AWAITING_INPUT:
            raise SessionNotAwaitingInputError(integration_session.status)

        try:
            target, flow = await self._current_target(
                client, integration_session
            )
        except StepFailedError as failure:
            await self._fail(integration_session, failure)
            await self.session.commit()
            return self._to_response(integration_session)
        self._reject_unexpected(flow, request.inputs)
        policies.check_input_formats(flow, request.inputs)

        if not await self.sessions.claim(
            integration_session, SessionStatus.AWAITING_INPUT
        ):
            raise SessionNotAwaitingInputError(SessionStatus.PROCESSING)
        await self.session.commit()

        try:
            state = FlowState.from_secret(
                await self.secret_store.read(
                    integration_session.state_secret_reference or ""
                )
            )
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "session_state_unavailable",
                    "The session's inputs could not be read.",
                ),
            )
            await self.session.commit()
            return self._to_response(integration_session)

        state.inputs.update(request.inputs)
        await self._advance(integration_session, client, target, flow, state)
        await self.session.commit()
        return self._to_response(integration_session)

    async def get(
        self, client: Client, session_id: uuid.UUID
    ) -> SessionResponse:
        """Return a session's status.

        Raises:
            SessionNotFoundError: The client has no such session.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if await self._expire_if_due(integration_session):
            await self.session.commit()
        return self._to_response(integration_session)

    async def list_for_user(
        self, client: Client, user_uuid: uuid.UUID, filters: SessionFilters
    ) -> Page[SessionResponse]:
        """Return one page of a user's sessions, newest first."""
        found, total = await self.sessions.list_page(
            client.id,
            user_uuid,
            status=filters.status,
            integration_type_code=filters.integration_type,
            limit=filters.limit,
            offset=filters.offset,
        )
        return Page[SessionResponse](
            items=[self._to_response(item) for item in found],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def list_verified_users(
        self, client: Client, filters: VerifiedUserFilters
    ) -> Page[VerifiedUserResponse]:
        """Return the client's users whose KYC succeeded, newest first.

        Each user appears once, with their most recent successful
        verification; no personal data is returned.
        """
        found, total = await self.sessions.verified_users_page(
            client.id,
            integration_type_code=filters.integration_type,
            flow=filters.flow,
            verified_after=filters.verified_after,
            user_uuid=filters.user_uuid,
            limit=filters.limit,
            offset=filters.offset,
        )
        return Page[VerifiedUserResponse](
            items=[
                VerifiedUserResponse(
                    user_uuid=item.user_uuid,
                    verified_at=item.completed_at,
                    session_id=item.id,
                    integration_type=item.integration_type.code,
                    tool_code=item.integration_tool.code,
                    flow=item.flow,
                )
                for item in found
            ],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def result(
        self, client: Client, session_id: uuid.UUID
    ) -> SessionResultResponse:
        """Return the attributes of a completed session.

        Raises:
            SessionNotFoundError: The client has no such session.
            ResultNotReadyError: The session has not completed.
            ResultExpiredError: The retention window has passed.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if (
            integration_session.status != SessionStatus.COMPLETED
            or integration_session.outcome is None
        ):
            raise ResultNotReadyError(integration_session.status)

        attributes: dict[str, Any] = {}
        fields: dict[str, Any] = {}
        if (
            integration_session.result_attributes
            or integration_session.result_fields
        ):
            expires = integration_session.data_expires_at
            reference = integration_session.result_secret_reference
            if reference is None or (
                expires is not None and expires <= utc_now()
            ):
                raise ResultExpiredError
            attributes, fields = policies.unpack_result(
                await self.secret_store.read(reference)
            )
        logger.info(
            "Result of session %s read by client %s",
            integration_session.id,
            client.id,
        )
        key_by_field_id = {
            requested["id"]: requested["key"]
            for requested in integration_session.requested_fields or []
        }
        return SessionResultResponse(
            session_id=integration_session.id,
            outcome=SessionOutcome(integration_session.outcome),
            attributes=attributes,
            fields=[
                SessionResultField(
                    field_id=uuid.UUID(field_id),
                    key=key_by_field_id[field_id],
                    value=value,
                )
                for field_id, value in fields.items()
                if field_id in key_by_field_id
            ],
            data_expires_at=integration_session.data_expires_at,
        )

    async def erase_result(
        self, client: Client, session_id: uuid.UUID
    ) -> None:
        """Delete a result now that the client has used it.

        DPDP section 8(7): personal data is erased once its purpose is
        served. Erasing an already erased result does nothing.

        Raises:
            SessionNotFoundError: The client has no such session.
            ResultNotReadyError: The session is still running.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if integration_session.is_active:
            raise ResultNotReadyError(integration_session.status)
        await self._erase_result(integration_session)
        await self.session.commit()
        logger.info(
            "Result of session %s erased by client %s",
            integration_session.id,
            client.id,
        )

    async def withdraw_consent(
        self, client: Client, session_id: uuid.UUID
    ) -> SessionResponse:
        """Record that the holder withdrew consent, and stop processing.

        DPDP section 6(4)-(6): an unfinished session is cancelled, its
        inputs and any result are deleted, and the withdrawal time is
        kept as evidence. Withdrawing again changes nothing.

        Raises:
            SessionNotFoundError: The client has no such session.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if integration_session.consent_withdrawn_at is not None:
            return self._to_response(integration_session)
        now = utc_now()
        if integration_session.is_active:
            integration_session.status = SessionStatus.CANCELLED
            integration_session.completed_at = now
            integration_session.awaiting_inputs = []
            integration_session.oauth_state_hash = None
            await self._discard_state(integration_session)
        await self._erase_result(integration_session)
        integration_session.consent_withdrawn_at = now
        await self.session.commit()
        logger.info(
            "Consent withdrawn for session %s of client %s",
            integration_session.id,
            client.id,
        )
        return self._to_response(integration_session)

    async def cancel(
        self, client: Client, session_id: uuid.UUID
    ) -> SessionResponse:
        """Stop an unfinished session and discard its inputs.

        Raises:
            SessionNotFoundError: The client has no such session.
            SessionNotActiveError: It has already finished.
        """
        integration_session = await self._get_or_raise(client, session_id)
        if not integration_session.is_active:
            raise SessionNotActiveError(integration_session.status)
        integration_session.status = SessionStatus.CANCELLED
        integration_session.completed_at = utc_now()
        integration_session.awaiting_inputs = []
        integration_session.oauth_state_hash = None
        await self._discard_state(integration_session)
        await self.session.commit()
        logger.info("Session %s cancelled", integration_session.id)
        return self._to_response(integration_session)

    async def expire_due(self, limit: int = 50) -> int:
        """Expire sessions past ``expires_at``; used by the worker."""
        due = await self.sessions.active_past_expiry(utc_now(), limit)
        for integration_session in due:
            await self._mark_expired(integration_session)
            await self.session.commit()
        return len(due)

    async def purge_results(self, limit: int = 50) -> int:
        """Delete results past their retention; used by the worker."""
        due = await self.sessions.results_past_retention(utc_now(), limit)
        purged = 0
        for integration_session in due:
            try:
                await self.secret_store.delete(
                    integration_session.result_secret_reference or ""
                )
            except SecretStoreError:
                # Kept for the next run rather than losing track of it.
                logger.warning(
                    "Result of session %s could not be deleted yet",
                    integration_session.id,
                )
                continue
            integration_session.result_secret_reference = None
            await self.session.commit()
            purged += 1
        return purged

    async def _advance(
        self,
        integration_session: IntegrationSession,
        client: Client,
        target: IntegrationTarget,
        flow: FlowConfig,
        state: FlowState,
        initial_data: Any = None,
        user_access_token: str | None = None,
    ) -> None:
        """Run steps from ``current_step`` until paused or finished.

        ``initial_data`` is what a completed sign-in returned (the ID
        token claims). Values are captured from it first, and it is the
        final data of a flow that has no steps. ``user_access_token``
        is the signed-in user's token for flows with ``steps_as_user``;
        it only lives for this call and is never put in ``state``.
        """
        if (
            initial_data is not None
            and flow.authorization is not None
            and not await self._capture(
                integration_session,
                CODE_EXCHANGE_OPERATION,
                flow.authorization.capture,
                initial_data,
                state,
            )
        ):
            return
        integration_session.status = SessionStatus.PROCESSING
        integration_session.awaiting_inputs = []
        try:
            credentials = await self.targets.load_credentials(
                client, target.tool
            )
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "tool_credentials_unavailable",
                    "The tool's credentials could not be read.",
                ),
            )
            return

        last_data: Any = initial_data
        steps = flow.steps
        while integration_session.current_step < len(steps):
            step = steps[integration_session.current_step]
            missing = [
                name for name in step.inputs if name not in state.inputs
            ]
            if missing:
                await self._pause(integration_session, state, missing)
                return

            connector = await self.targets.build_connector(
                target,
                client,
                integration_session.user_uuid,
                credentials=credentials,
                session_values=state.captured,
                user_access_token=user_access_token,
            )
            try:
                outcome = await self._run_step(
                    integration_session,
                    client,
                    target,
                    step.operation,
                    connector.run_operation,
                    state.inputs,
                )
            except StepFailedError as failure:
                await self._fail(integration_session, failure)
                return

            if not outcome.success:
                await self._provider_rejected(integration_session)
                return
            if not await self._capture(
                integration_session,
                step.operation,
                step.capture,
                outcome.data,
                state,
            ):
                return
            last_data = outcome.data
            integration_session.current_step += 1

        await self._complete(
            integration_session, client, target, flow, state, last_data
        )

    async def _run_step(
        self,
        integration_session: IntegrationSession,
        client: Client,
        target: IntegrationTarget,
        operation: str,
        run_operation: Any,
        inputs: dict[str, Any],
    ) -> OperationOutcome:
        """Call one operation, log it, and turn errors into failures."""
        started_at = time.perf_counter()
        outcome: OperationOutcome | None = None
        failure: StepFailedError | None = None
        try:
            async with asyncio.timeout(
                self.settings.connector_timeout_seconds
            ):
                outcome = await run_operation(operation, **inputs)
        except UnknownOperationError:
            failure = StepFailedError(
                "flow_misconfigured",
                f"The tool no longer offers operation '{operation}'.",
            )
        except MissingParametersError as exc:
            error: AppError = missing_inputs_error(target.tool.code, exc)
            failure = StepFailedError(error.error_code, error.message)
        except InvalidInputError as exc:
            failure = StepFailedError(
                "invalid_inputs", f"{', '.join(exc.names)}: {exc.reason}"
            )
        except ConnectorError as exc:
            # Connector messages are written to be safe to show clients.
            failure = StepFailedError("provider_error", str(exc))
        except TimeoutError:
            failure = StepFailedError(
                "provider_timeout", "The integration did not respond in time."
            )
        except Exception as exc:
            # Never log the exception message: it may echo inputs or
            # credentials. The type and stack frames are enough.
            logger.error(
                "Session %s step %s raised %s:\n%s",
                integration_session.id,
                operation,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            failure = StepFailedError(
                "unexpected_error", "The integration failed unexpectedly."
            )

        self.session.add(
            IntegrationOperationLog(
                client_id=client.id,
                user_uuid=integration_session.user_uuid,
                integration_type_code=target.integration_type.code,
                tool_code=target.tool.code,
                operation=operation[:64],
                succeeded=bool(outcome and outcome.success),
                provider_status_code=outcome.status_code if outcome else None,
                error_code=failure.code if failure else None,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                request_id=get_request_id(),
            )
        )
        if failure is not None or outcome is None:
            raise failure or StepFailedError(
                "unexpected_error", "The integration failed unexpectedly."
            )
        return outcome

    async def _pause(
        self,
        integration_session: IntegrationSession,
        state: FlowState,
        missing: list[str],
    ) -> None:
        """Save progress and wait for ``missing`` inputs."""
        try:
            await self._save_state(integration_session, state)
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "session_state_unavailable",
                    "The session's inputs could not be stored.",
                ),
            )
            return
        integration_session.status = SessionStatus.AWAITING_INPUT
        integration_session.awaiting_inputs = missing
        logger.info(
            "Session %s waiting for %s",
            integration_session.id,
            ", ".join(missing),
        )

    async def _begin_authorization(
        self,
        integration_session: IntegrationSession,
        client: Client,
        target: IntegrationTarget,
        auth: OAuth2AuthorizationCode,
        state: FlowState,
        redirect_uri: str,
    ) -> SessionNextAction | None:
        """Prepare the provider sign-in and wait for the holder to return.

        Returns the action for the holder, or None when the session
        failed instead (credentials missing or unreadable).
        """
        try:
            credentials = await self.targets.load_credentials(
                client, target.tool
            )
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "tool_credentials_unavailable",
                    "The tool's credentials could not be read.",
                ),
            )
            return None
        request = authorization.AuthorizationRequest.new()
        try:
            url = authorization.build_authorization_url(
                auth,
                {
                    "credentials": credentials,
                    "settings": target.settings,
                },
                request,
                redirect_uri,
            )
        except MissingParametersError as exc:
            error = missing_inputs_error(target.tool.code, exc)
            await self._fail(
                integration_session,
                StepFailedError(error.error_code, error.message),
            )
            return None

        state.pending_authorization = {
            "nonce": request.nonce,
            "code_verifier": request.code_verifier,
            "redirect_uri": redirect_uri,
        }
        try:
            await self._save_state(integration_session, state)
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "session_state_unavailable",
                    "The session's sign-in details could not be stored.",
                ),
            )
            return None
        integration_session.oauth_state_hash = authorization.hash_state(
            request.state
        )
        integration_session.status = SessionStatus.AWAITING_REDIRECT
        logger.info("Session %s waiting for sign-in", integration_session.id)
        return SessionNextAction(type="redirect", url=url)

    async def _exchange_code(
        self,
        integration_session: IntegrationSession,
        client: Client,
        target: IntegrationTarget,
        auth: OAuth2AuthorizationCode,
        code: str,
        pending: dict[str, str],
    ) -> authorization.SignInResult | None:
        """Redeem the code; return the sign-in, or None if the session failed.

        Runs through :meth:`_run_step` so the exchange is timed out,
        logged, and its errors mapped like any provider operation. The
        access token stays out of the step's outcome, which is logged.
        """
        try:
            credentials = await self.targets.load_credentials(
                client, target.tool
            )
        except SecretStoreError:
            await self._fail(
                integration_session,
                StepFailedError(
                    "tool_credentials_unavailable",
                    "The tool's credentials could not be read.",
                ),
            )
            return None
        values = {
            "credentials": credentials,
            "settings": target.settings,
        }

        redeemed: list[authorization.SignInResult] = []

        async def exchange(operation: str) -> OperationOutcome:
            sign_in = await authorization.exchange_code(
                self.targets.http_client,
                auth,
                values,
                code=code,
                redirect_uri=pending["redirect_uri"],
                code_verifier=pending["code_verifier"],
                nonce=pending["nonce"],
                timeout_seconds=self.settings.connector_timeout_seconds,
            )
            redeemed.append(sign_in)
            return OperationOutcome(success=True, data=sign_in.claims)

        try:
            await self._run_step(
                integration_session,
                client,
                target,
                CODE_EXCHANGE_OPERATION,
                exchange,
                {},
            )
        except StepFailedError as failure:
            await self._fail(integration_session, failure)
            return None
        return redeemed[0]

    async def _capture(
        self,
        integration_session: IntegrationSession,
        operation: str,
        capture: dict[str, str],
        data: Any,
        state: FlowState,
    ) -> bool:
        """Copy captured values into ``state``; False if the session failed."""
        for name, path in capture.items():
            value = read_path(data, path)
            if value is MISSING:
                await self._fail(
                    integration_session,
                    StepFailedError(
                        "unexpected_response",
                        f"The provider's response to '{operation}' had "
                        f"no '{path}'.",
                    ),
                )
                return False
            state.captured[name] = value
        return True

    @staticmethod
    def _kept_attributes(
        integration_session: IntegrationSession, attributes: dict[str, Any]
    ) -> dict[str, Any]:
        """Drop outputs the client did not ask for (data minimisation)."""
        requested = integration_session.requested_attributes
        if requested is None:
            # Asking only for fields means no outputs are wanted.
            return {} if integration_session.requested_fields else attributes
        return {
            name: value
            for name, value in attributes.items()
            if name in set(requested)
        }

    async def _requested_fields(
        self,
        client: Client,
        target: IntegrationTarget,
        request: SessionCreate,
    ) -> list[dict[str, str]] | None:
        """Resolve requested field ids to keys of this flow.

        Raises:
            UnknownRequestedFieldsError: An id is not a field of the
                flow that this client can see.
        """
        if request.requested_fields is None:
            return None
        requested_ids = set(request.requested_fields)
        found = await self.tool_fields.list_visible_to_client(
            target.tool.id,
            client.id,
            flow=request.flow,
            field_ids=requested_ids,
        )
        missing = sorted(
            requested_ids - {tool_field.id for tool_field in found}
        )
        if missing:
            raise UnknownRequestedFieldsError(missing)
        return [
            {"id": str(tool_field.id), "key": tool_field.key}
            for tool_field in found
        ]

    async def _erase_result(
        self, integration_session: IntegrationSession
    ) -> None:
        """Delete the stored result now, or leave it to the worker.

        If the secret store is unreachable, ``data_expires_at`` is moved
        to now so the worker's purge deletes it on its next round.
        """
        reference = integration_session.result_secret_reference
        if reference is None:
            return
        now = utc_now()
        integration_session.data_expires_at = now
        try:
            await self.secret_store.delete(reference)
        except SecretStoreError:
            logger.warning(
                "Result of session %s left for the worker to delete",
                integration_session.id,
            )
            return
        integration_session.result_secret_reference = None

    @staticmethod
    def _describe_input(flow: FlowConfig, name: str) -> FlowInputResponse:
        spec = flow.input_specs.get(name)
        if spec is None:
            return FlowInputResponse(
                name=name,
                label=name.replace("_", " ").capitalize(),
                input_type="text",
                pattern=None,
                min_length=None,
                max_length=None,
                is_masked=False,
                hint=None,
            )
        return FlowInputResponse(
            name=name,
            label=spec.label,
            input_type=spec.input_type,
            pattern=spec.pattern,
            min_length=spec.min_length,
            max_length=spec.max_length,
            is_masked=spec.is_masked,
            hint=spec.hint,
        )

    async def _complete(
        self,
        integration_session: IntegrationSession,
        client: Client,
        target: IntegrationTarget,
        flow: FlowConfig,
        state: FlowState,
        data: Any,
    ) -> None:
        """Decide the outcome, store the result, and finish."""
        if flow.purpose == SessionPurpose.VERIFICATION and not all(
            condition_holds(data, condition)
            for condition in flow.verified_when
        ):
            await self._provider_rejected(integration_session)
            return

        attributes = self._kept_attributes(
            integration_session,
            map_outputs(flow.outputs, data, state.captured),
        )
        fields = {
            requested["id"]: value
            for requested in integration_session.requested_fields or []
            if (value := read_path(data, requested["key"])) is not MISSING
        }
        now = utc_now()
        if attributes or fields:
            try:
                integration_session.result_secret_reference = (
                    await self.secret_store.create(
                        result_secret_name(client.id, integration_session.id),
                        policies.pack_result(attributes, fields),
                        tags={
                            "client_id": str(client.id),
                            "purpose": "session_result",
                        },
                    )
                )
            except SecretStoreError:
                await self._fail(
                    integration_session,
                    StepFailedError(
                        "result_unavailable",
                        "The result could not be stored securely.",
                    ),
                )
                return
            integration_session.data_expires_at = now + timedelta(
                days=self.settings.session_data_retention_days
            )
        integration_session.result_attributes = sorted(attributes)
        integration_session.result_fields = sorted(fields)
        integration_session.outcome = (
            SessionOutcome.GATHERED
            if flow.purpose == SessionPurpose.GATHER
            else SessionOutcome.VERIFIED
        )
        await self._finish(integration_session, SessionStatus.COMPLETED)

    async def _provider_rejected(
        self, integration_session: IntegrationSession
    ) -> None:
        """The provider answered "no".

        For verification that is an answer: completed, not verified. A
        gather flow that is refused has nothing to return, so it fails.
        """
        if integration_session.purpose == SessionPurpose.VERIFICATION:
            integration_session.outcome = SessionOutcome.NOT_VERIFIED
            integration_session.failure_code = PROVIDER_REJECTED
            integration_session.failure_message = (
                "The provider could not verify the details given."
            )
            await self._finish(integration_session, SessionStatus.COMPLETED)
            return
        await self._fail(
            integration_session,
            StepFailedError(
                PROVIDER_REJECTED, "The provider declined the request."
            ),
        )

    async def _fail(
        self, integration_session: IntegrationSession, failure: StepFailedError
    ) -> None:
        integration_session.failure_code = failure.code
        integration_session.failure_message = failure.message
        logger.warning(
            "Session %s failed: %s", integration_session.id, failure.code
        )
        await self._finish(integration_session, SessionStatus.FAILED)

    async def _mark_expired(
        self, integration_session: IntegrationSession
    ) -> None:
        integration_session.failure_code = "session_expired"
        integration_session.failure_message = (
            "The session was not completed in time."
        )
        await self._finish(integration_session, SessionStatus.EXPIRED)

    async def _expire_if_due(
        self, integration_session: IntegrationSession
    ) -> bool:
        """Expire now instead of waiting for the worker; True if it did."""
        if (
            integration_session.is_active
            and integration_session.expires_at <= utc_now()
        ):
            await self._mark_expired(integration_session)
            return True
        return False

    async def _finish(
        self, integration_session: IntegrationSession, final: SessionStatus
    ) -> None:
        """Set the final status, drop the inputs, and queue the event."""
        integration_session.status = final
        integration_session.awaiting_inputs = []
        integration_session.oauth_state_hash = None
        integration_session.completed_at = utc_now()
        await self._discard_state(integration_session)
        await self.webhooks.enqueue(
            integration_session.client_id,
            FINAL_EVENTS[final],
            self._event_data(integration_session),
            session_id=integration_session.id,
        )

    async def _save_state(
        self, integration_session: IntegrationSession, state: FlowState
    ) -> None:
        if integration_session.state_secret_reference is None:
            integration_session.state_secret_reference = (
                await self.secret_store.create(
                    state_secret_name(
                        integration_session.client_id, integration_session.id
                    ),
                    state.to_secret(),
                    tags={
                        "client_id": str(integration_session.client_id),
                        "purpose": "session_state",
                    },
                )
            )
        else:
            await self.secret_store.replace(
                integration_session.state_secret_reference, state.to_secret()
            )

    async def _discard_state(
        self, integration_session: IntegrationSession
    ) -> None:
        reference = integration_session.state_secret_reference
        if reference is None:
            return
        try:
            await self.secret_store.delete(reference)
        except SecretStoreError:
            logger.warning(
                "Inputs of session %s could not be deleted",
                integration_session.id,
            )
            return
        integration_session.state_secret_reference = None

    async def _current_target(
        self, client: Client, integration_session: IntegrationSession
    ) -> tuple[IntegrationTarget, FlowConfig]:
        """Resolve the session's tool again; the setup may have changed.

        The session keeps the tool it started with, even if the type's
        default tool changes meanwhile.
        """
        try:
            target = await self.targets.resolve(
                client,
                integration_session.integration_type.code,
                integration_session.integration_tool.code,
            )
        except AppError as exc:
            raise StepFailedError(exc.error_code, exc.message) from exc
        flow = target.connector_class.describe_flows(
            target.tool.connector_config
        ).get(integration_session.flow)
        if flow is None:
            raise StepFailedError(
                "flow_misconfigured",
                "The tool no longer offers flow "
                f"'{integration_session.flow}'.",
            )
        return target, flow

    @staticmethod
    def _flow_or_raise(target: IntegrationTarget, name: str) -> FlowConfig:
        flow = target.connector_class.describe_flows(
            target.tool.connector_config
        ).get(name)
        if flow is None:
            raise FlowNotFoundError(target.tool.code, name)
        return flow

    @staticmethod
    def _reject_unexpected(flow: FlowConfig, inputs: dict[str, Any]) -> None:
        expected = set(flow.all_inputs())
        unexpected = sorted(name for name in inputs if name not in expected)
        if unexpected:
            raise UnexpectedInputsError(unexpected)

    async def _get_or_raise(
        self, client: Client, session_id: uuid.UUID
    ) -> IntegrationSession:
        integration_session = await self.sessions.get_for_client(
            session_id, client.id
        )
        if integration_session is None:
            raise SessionNotFoundError(session_id)
        return integration_session

    @staticmethod
    def _event_data(integration_session: IntegrationSession) -> dict[str, Any]:
        """Webhook payload: status only, never inputs or attributes."""
        return {
            "session_id": str(integration_session.id),
            "user_uuid": str(integration_session.user_uuid),
            "reference": integration_session.reference,
            "integration_type": integration_session.integration_type.code,
            "tool_code": integration_session.integration_tool.code,
            "flow": integration_session.flow,
            "purpose": integration_session.purpose,
            "status": integration_session.status,
            "outcome": integration_session.outcome,
            "failure_code": integration_session.failure_code,
            "result_attributes": list(integration_session.result_attributes),
            "result_fields": list(integration_session.result_fields),
        }

    @staticmethod
    def _to_consent_response(
        integration_session: IntegrationSession,
    ) -> SessionConsentResponse | None:
        if (
            integration_session.consented_at is None
            or integration_session.consent_purpose is None
            or integration_session.consent_notice_version is None
        ):
            return None
        return SessionConsentResponse(
            purpose=integration_session.consent_purpose,
            notice_version=integration_session.consent_notice_version,
            language=integration_session.consent_language or "en",
            consented_at=integration_session.consented_at,
            reference=integration_session.consent_reference,
            withdrawn_at=integration_session.consent_withdrawn_at,
        )

    @classmethod
    def _to_response(
        cls,
        integration_session: IntegrationSession,
        next_action: SessionNextAction | None = None,
    ) -> SessionResponse:
        return SessionResponse(
            id=integration_session.id,
            user_uuid=integration_session.user_uuid,
            integration_type=integration_session.integration_type.code,
            tool_code=integration_session.integration_tool.code,
            flow=integration_session.flow,
            purpose=SessionPurpose(integration_session.purpose),
            status=SessionStatus(integration_session.status),
            outcome=(
                SessionOutcome(integration_session.outcome)
                if integration_session.outcome
                else None
            ),
            awaiting_inputs=list(integration_session.awaiting_inputs),
            next_action=next_action,
            reference=integration_session.reference,
            failure_code=integration_session.failure_code,
            failure_message=integration_session.failure_message,
            result_attributes=list(integration_session.result_attributes),
            result_fields=[
                uuid.UUID(field_id)
                for field_id in integration_session.result_fields
            ],
            consent=cls._to_consent_response(integration_session),
            expires_at=integration_session.expires_at,
            completed_at=integration_session.completed_at,
            data_expires_at=integration_session.data_expires_at,
            created_at=integration_session.created_at,
            updated_at=integration_session.updated_at,
        )

"""Run verification and gather flows as resumable sessions.

A session runs the steps of one flow from the tool's configuration. It
runs as far as it can, pausing when a step needs an input the holder has
not given yet (an OTP sent by the previous step, typically), and resumes
when the client submits it. When the last step finishes, the flow's
outputs are read from the response and kept, encrypted, until the
retention window ends.

Nothing personal is stored in the database: inputs and captured values
live in the secret store while the session is active, and the result
only until ``data_expires_at``. Webhook events carry the session's
status, never its data.
"""

import asyncio
import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import (
    ConnectorError,
    InvalidInputError,
    MissingParametersError,
    OperationOutcome,
    UnknownOperationError,
)
from app.connectors.http.config import FlowCondition, FlowConfig
from app.core.config import Settings
from app.core.context import get_request_id
from app.core.data_paths import MISSING, describe_paths, read_path
from app.core.exceptions import AppError
from app.core.models import utc_now
from app.core.secret_store import SecretStore, SecretStoreError
from app.features.clients.models import Client
from app.features.integration_tools.repository import (
    IntegrationToolFieldRepository,
)
from app.features.sessions.exceptions import (
    FlowNotFoundError,
    ResultExpiredError,
    ResultNotReadyError,
    SessionNotActiveError,
    SessionNotAwaitingInputError,
    SessionNotFoundError,
    UnexpectedInputsError,
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
    SessionCreate,
    SessionFilters,
    SessionInputs,
    SessionResponse,
    SessionResultResponse,
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
    """

    inputs: dict[str, Any] = field(default_factory=dict)
    captured: dict[str, Any] = field(default_factory=dict)

    def to_secret(self) -> dict[str, Any]:
        """Serialise for the secret store."""
        return {"inputs": self.inputs, "captured": self.captured}

    @classmethod
    def from_secret(cls, value: dict[str, Any]) -> "FlowState":
        """Rebuild from what :meth:`to_secret` stored."""
        return cls(
            inputs=dict(value.get("inputs", {})),
            captured=dict(value.get("captured", {})),
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
        secret_store: Holds session inputs and results.
        webhooks: Queues status events for the client.
        settings: Timeouts, session lifetime, and retention.
    """

    def __init__(
        self,
        session: AsyncSession,
        sessions: SessionRepository,
        targets: IntegrationTargetResolver,
        secret_store: SecretStore,
        webhooks: WebhookService,
        settings: Settings,
    ) -> None:
        self.session = session
        self.sessions = sessions
        self.targets = targets
        # Field keys seen in completed sessions, written after commit.
        self._seen_fields: list[tuple[uuid.UUID, str, dict[str, str]]] = []
        self.secret_store = secret_store
        self.webhooks = webhooks
        self.settings = settings

    async def start(
        self, client: Client, user_uuid: uuid.UUID, request: SessionCreate
    ) -> SessionResponse:
        """Create a session and run it until it needs input or finishes.

        Problems with the setup (type not enabled, unknown flow) are
        raised before anything is stored. Problems with the provider
        are recorded on the session, which is returned as ``failed``.

        Raises:
            IntegrationTypeNotFoundError: Unknown integration type.
            IntegrationNotEnabledError: Not enabled for the client.
            IntegrationToolNotSelectedError: No tool chosen for the type.
            IntegrationToolInactiveError: The chosen tool is inactive.
            IntegrationToolDisabledError: The client switched it off.
            ConnectorNotAvailableError: The tool has no connector.
            FlowNotFoundError: The tool has no such flow.
            UnexpectedInputsError: Inputs the flow never uses were sent.
        """
        target = await self.targets.resolve(client, request.integration_type)
        flow = self._flow_or_raise(target, request.flow)
        self._reject_unexpected(flow, request.inputs)

        now = utc_now()
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
            reference=request.reference,
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
        await self._advance(integration_session, client, target, flow, state)
        await self._commit_and_record_fields()
        return self._to_response(integration_session)

    async def submit_inputs(
        self, client: Client, session_id: uuid.UUID, request: SessionInputs
    ) -> SessionResponse:
        """Add inputs to a waiting session and continue running it.

        Raises:
            SessionNotFoundError: The client has no such session.
            SessionNotActiveError: The session has finished or expired.
            SessionNotAwaitingInputError: It is busy processing.
            UnexpectedInputsError: Inputs the flow never uses were sent.
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
        await self._commit_and_record_fields()
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
        if integration_session.result_attributes:
            expires = integration_session.data_expires_at
            reference = integration_session.result_secret_reference
            if reference is None or (
                expires is not None and expires <= utc_now()
            ):
                raise ResultExpiredError
            attributes = await self.secret_store.read(reference)
        logger.info(
            "Result of session %s read by client %s",
            integration_session.id,
            client.id,
        )
        return SessionResultResponse(
            session_id=integration_session.id,
            outcome=SessionOutcome(integration_session.outcome),
            attributes=attributes,
            data_expires_at=integration_session.data_expires_at,
        )

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
    ) -> None:
        """Run steps from ``current_step`` until paused or finished."""
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

        last_data: Any = None
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
            for name, path in step.capture.items():
                value = read_path(outcome.data, path)
                if value is MISSING:
                    await self._fail(
                        integration_session,
                        StepFailedError(
                            "unexpected_response",
                            f"The provider's response to "
                            f"'{step.operation}' had no '{path}'.",
                        ),
                    )
                    return
                state.captured[name] = value
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
        # Keys and types only; recorded whatever the outcome, since the
        # provider answered either way.
        self._seen_fields.append(
            (target.tool.id, integration_session.flow, describe_paths(data))
        )
        if flow.purpose == SessionPurpose.VERIFICATION and not all(
            condition_holds(data, condition)
            for condition in flow.verified_when
        ):
            await self._provider_rejected(integration_session)
            return

        attributes = map_outputs(flow.outputs, data, state.captured)
        now = utc_now()
        if attributes:
            try:
                integration_session.result_secret_reference = (
                    await self.secret_store.create(
                        result_secret_name(client.id, integration_session.id),
                        attributes,
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
        """Resolve the tool again; the setup may have changed meanwhile."""
        try:
            target = await self.targets.resolve(
                client, integration_session.integration_type.code
            )
        except AppError as exc:
            raise StepFailedError(exc.error_code, exc.message) from exc
        if target.tool.id != integration_session.integration_tool_id:
            raise StepFailedError(
                "integration_tool_changed",
                "The client switched tools while the session was open.",
            )
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

    async def _commit_and_record_fields(self) -> None:
        """Commit the session, then record the field keys it saw.

        The keys are written in a separate transaction after the session
        is committed, so a failure here (two sessions recording the same
        new key at once, say) never undoes or fails the session itself;
        the keys are recorded again by the next completed session.
        """
        await self.session.commit()
        seen, self._seen_fields = self._seen_fields, []
        if not seen:
            return
        try:
            async with AsyncSession(
                self.session.bind, expire_on_commit=False
            ) as fields_session:
                fields = IntegrationToolFieldRepository(fields_session)
                for tool_id, flow_name, types_by_key in seen:
                    new_keys = await fields.record(
                        tool_id, flow_name, types_by_key, utc_now()
                    )
                    if new_keys:
                        logger.info(
                            "Recorded %s new field keys for flow %s",
                            new_keys,
                            flow_name,
                        )
                await fields_session.commit()
        except SQLAlchemyError:
            logger.warning(
                "Field keys could not be recorded; the next completed "
                "session will record them",
                exc_info=True,
            )

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
        }

    @staticmethod
    def _to_response(
        integration_session: IntegrationSession,
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
            reference=integration_session.reference,
            failure_code=integration_session.failure_code,
            failure_message=integration_session.failure_message,
            result_attributes=list(integration_session.result_attributes),
            expires_at=integration_session.expires_at,
            completed_at=integration_session.completed_at,
            data_expires_at=integration_session.data_expires_at,
            created_at=integration_session.created_at,
            updated_at=integration_session.updated_at,
        )

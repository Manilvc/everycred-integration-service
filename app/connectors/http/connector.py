"""Generic connector that runs operations described by configuration.

Any tool whose ``connector_config`` is an
:class:`~app.connectors.http.config.HttpConnectorConfig` is served by
:class:`HttpConnector`; adding a provider such as SurePass is a matter
of saving its configuration and credentials, not writing code.
"""

import logging
from typing import Any

import httpx

from app.connectors.base import (
    ConnectionOutcome,
    ConnectionRequirements,
    ConnectionTestOutcome,
    ConnectorError,
    IntegrationConnector,
    InvalidInputError,
    OperationDescription,
    OperationOutcome,
    UnknownOperationError,
)
from app.connectors.http import oauth
from app.connectors.http.config import (
    HttpConnectorConfig,
    OperationConfig,
    ResponseMapping,
)
from app.connectors.http.templates import (
    find_placeholders,
    render,
    url_encode_values,
)

logger = logging.getLogger(__name__)

ENVIRONMENT_SETTING = "environment"
_BODYLESS_METHODS = {"GET", "DELETE"}


class HttpConnector(IntegrationConnector):
    """Runs the operations listed in the tool's ``connector_config``.

    The provider environment (sandbox or production) comes from the
    client's ``environment`` setting and must be one the tool defines;
    clients can pick an environment but never supply a URL.
    """

    @property
    def config(self) -> HttpConnectorConfig:
        """The validated configuration of the tool being called."""
        return HttpConnectorConfig.model_validate(self.context.tool_config)

    async def connect(self, *args: Any, **kwargs: Any) -> ConnectionOutcome:
        """Check the user's inputs, running ``connect_operation`` if set.

        Raises:
            MissingParametersError: A required input is missing.
            ConnectorError: The check call failed or was refused.
        """
        config = self.config
        environment = self._environment(config)
        if config.connect_operation is None:
            # Nothing to call: render the headers and auth so a missing
            # credential is reported now rather than later.
            self._check_connection_inputs(config, self._values(args, kwargs))
            return ConnectionOutcome(details={"environment": environment})

        outcome = await self.run_operation(
            config.connect_operation, *args, **kwargs
        )
        if not outcome.success:
            raise ConnectorError(
                outcome.message or "The provider rejected the connection."
            )
        return ConnectionOutcome(details={"environment": environment})

    async def test_connection(self) -> ConnectionTestOutcome:
        """Check the client's credentials without any user.

        In order of preference: run ``test_operation``; otherwise obtain
        a fresh OAuth token; otherwise only confirm that every credential
        the configuration uses is stored.

        Raises:
            MissingParametersError: A credential or setting is missing.
            ConnectorError: The provider refused or could not be reached.
        """
        config = self.config
        environment = self._environment(config)
        values = self._values((), {})
        if config.test_operation is not None:
            outcome = await self.run_operation(config.test_operation)
            if not outcome.success:
                raise ConnectorError(
                    outcome.message or "The provider rejected the test call."
                )
            return ConnectionTestOutcome(
                called_provider=True,
                message=f"Test call succeeded ({environment}).",
            )
        if config.auth is not None:
            key = oauth.cache_key(
                self.context.tool_code,
                self.context.client_id,
                config.auth,
                values,
            )
            # A test must prove the credentials work now, so skip the cache.
            oauth.token_cache.invalidate(key)
            await self._access_token(config, values)
            return ConnectionTestOutcome(
                called_provider=True,
                message=(
                    "Obtained an access token with the stored credentials."
                ),
            )
        self._check_connection_inputs(config, values)
        return ConnectionTestOutcome(
            called_provider=False,
            message=(
                "All credentials are stored. This tool defines no test "
                "call, so they were not checked with the provider."
            ),
        )

    async def run_operation(
        self, operation: str, *args: Any, **kwargs: Any
    ) -> OperationOutcome:
        """Send the configured request and normalise the response.

        With OAuth, a ``401`` using a cached token is retried once with a
        fresh token, since the provider may have revoked it early.

        Raises:
            UnknownOperationError: The tool has no such operation.
            MissingParametersError: A placeholder has no value.
            ConnectorError: The provider could not be reached, timed
                out, or returned something that is not JSON.
        """
        config = self.config
        operation_config = config.operations.get(operation)
        if operation_config is None:
            raise UnknownOperationError(operation)

        values = self._values(args, kwargs)
        request = self._build_request(config, operation_config, values)
        response = await self._send(config, request, values, operation)
        if response.status_code == 401 and config.auth is not None:
            oauth.token_cache.invalidate(
                oauth.cache_key(
                    self.context.tool_code,
                    self.context.client_id,
                    config.auth,
                    values,
                )
            )
            response = await self._send(config, request, values, operation)

        return self._parse_response(
            response, operation_config.response or config.response
        )

    @classmethod
    def describe_requirements(
        cls, tool_config: dict[str, Any] | None
    ) -> ConnectionRequirements:
        """Describe the configured auth and the credentials it needs."""
        if not tool_config:
            return super().describe_requirements(tool_config)
        config = HttpConnectorConfig.model_validate(tool_config)
        return ConnectionRequirements(
            auth_method=config.auth_method,
            required_credentials=config.required_credentials(),
            can_test=config.test_operation is not None
            or config.auth is not None,
        )

    @classmethod
    def describe_operations(
        cls, tool_config: dict[str, Any] | None
    ) -> list[OperationDescription]:
        """List configured operations and the inputs each one reads."""
        if not tool_config:
            return []
        config = HttpConnectorConfig.model_validate(tool_config)
        descriptions = []
        for name, operation in config.operations.items():
            inputs = config.required_inputs(name)
            descriptions.append(
                OperationDescription(
                    name=name,
                    description=operation.description,
                    required_kwargs=_names_from(inputs, "kwargs"),
                    required_credentials=_names_from(inputs, "credentials"),
                )
            )
        return descriptions

    async def _send(
        self,
        config: HttpConnectorConfig,
        request: dict[str, Any],
        values: dict[str, Any],
        operation: str,
    ) -> httpx.Response:
        headers = dict(request["headers"])
        if config.auth is not None:
            headers["Authorization"] = (
                f"Bearer {await self._access_token(config, values)}"
            )
        try:
            return await self.context.http_client.request(
                **{**request, "headers": headers},
                timeout=config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ConnectorError(
                "The provider did not respond in time."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "%s request for %s failed: %s",
                self.context.tool_code,
                operation,
                type(exc).__name__,
            )
            raise ConnectorError("The provider could not be reached.") from exc

    async def _access_token(
        self, config: HttpConnectorConfig, values: dict[str, Any]
    ) -> str:
        auth = config.auth
        if auth is None:
            raise ConnectorError("This tool does not use OAuth.")

        async def fetch() -> tuple[str, int]:
            return await oauth.request_token(
                self.context.http_client,
                auth,
                values,
                config.timeout_seconds,
            )

        key = oauth.cache_key(
            self.context.tool_code, self.context.client_id, auth, values
        )
        return await oauth.token_cache.get(key, fetch)

    @staticmethod
    def _connection_templates(config: HttpConnectorConfig) -> list[Any]:
        templates: list[Any] = [config.headers]
        if config.auth is not None:
            templates.append(
                [
                    config.auth.token_url,
                    config.auth.client_id,
                    config.auth.client_secret,
                ]
            )
        return templates

    def _check_connection_inputs(
        self, config: HttpConnectorConfig, values: dict[str, Any]
    ) -> None:
        render(self._connection_templates(config), values)

    def _environment(self, config: HttpConnectorConfig) -> str:
        requested = self.context.client_settings.get(ENVIRONMENT_SETTING)
        environment = requested or config.default_environment
        if environment not in config.environments:
            raise ConnectorError(
                f"Environment '{environment}' is not offered by this tool."
            )
        return environment

    def _values(self, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
        user_uuid = self.context.user_uuid
        return {
            "args": list(args),
            "kwargs": kwargs,
            "credentials": self.context.credentials,
            "settings": self.context.client_settings,
            "user_uuid": str(user_uuid) if user_uuid else None,
            "client_id": str(self.context.client_id),
        }

    def _build_request(
        self,
        config: HttpConnectorConfig,
        operation: OperationConfig,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        # Render everything once up front so every missing input and
        # credential is reported together, not one group per attempt.
        render(
            [
                self._connection_templates(config),
                operation.path,
                operation.query,
                operation.body,
            ],
            values,
        )
        self._reject_unsafe_path_values(operation.path, values)
        base_url = config.environments[self._environment(config)]
        rendered_path = render(operation.path, url_encode_values(values))
        url = f"{base_url}{rendered_path}"
        self._check_stays_on_base(url, base_url)
        request: dict[str, Any] = {
            "method": operation.method,
            "url": url,
            "headers": {
                name: str(value)
                for name, value in render(config.headers, values).items()
            },
            "params": render(operation.query, values) or None,
        }
        if operation.body is not None and operation.method not in (
            _BODYLESS_METHODS
        ):
            request["json"] = render(operation.body, values)
        return request

    @staticmethod
    def _reject_unsafe_path_values(
        path_template: str, values: dict[str, Any]
    ) -> None:
        # "/" is percent-encoded, but "." and ".." are not special to
        # encoding, and HTTP clients resolve them as dot segments. An
        # input of ".." would otherwise move the request to another
        # endpoint while still carrying the client's credentials.
        unsafe = [
            placeholder
            for placeholder in sorted(find_placeholders(path_template))
            if str(render(f"{{{placeholder}}}", values)).strip()
            in {"", ".", ".."}
        ]
        if unsafe:
            raise InvalidInputError(
                unsafe, "path inputs may not be empty, '.', or '..'"
            )

    @staticmethod
    def _check_stays_on_base(url: str, base_url: str) -> None:
        # Second line of defence: after the client library normalises
        # the URL, it must still be on the configured host and under the
        # configured base path.
        target, base = httpx.URL(url), httpx.URL(base_url)
        base_path = base.path.rstrip("/") + "/"
        if target.host != base.host or not (target.path + "/").startswith(
            base_path
        ):
            raise InvalidInputError(
                ["path"], "the request would leave the configured base URL"
            )

    def _parse_response(
        self, response: httpx.Response, mapping: ResponseMapping
    ) -> OperationOutcome:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(
                f"The provider returned a non-JSON response "
                f"(HTTP {response.status_code})."
            ) from exc

        succeeded = response.is_success
        data: Any = payload
        message = None
        if isinstance(payload, dict):
            if mapping.success_field and mapping.success_field in payload:
                succeeded = succeeded and bool(payload[mapping.success_field])
            if mapping.data_field:
                data = payload.get(mapping.data_field)
            if mapping.message_field:
                raw_message = payload.get(mapping.message_field)
                message = str(raw_message) if raw_message is not None else None

        return OperationOutcome(
            success=succeeded,
            status_code=response.status_code,
            data=data,
            message=message,
        )


def _names_from(placeholders: set[str], source: str) -> list[str]:
    return sorted(
        placeholder.split(".", 1)[1]
        for placeholder in placeholders
        if placeholder.startswith(f"{source}.")
    )

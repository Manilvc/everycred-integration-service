"""Schema of a configuration-driven HTTP connector.

A tool whose ``connector_config`` has ``"type": "http"`` is served by
:class:`~app.connectors.http.connector.HttpConnector` without any tool
specific code. The configuration says where the provider lives, how to
authenticate, and which operations exist::

    {
      "type": "http",
      "environments": {
        "sandbox": "https://sandbox.example.com/api/v1",
        "production": "https://api.example.com/api/v1"
      },
      "default_environment": "sandbox",
      "headers": {"Authorization": "Bearer {credentials.api_token}"},
      "operations": {
        "verify_document": {
          "method": "POST",
          "path": "/documents/verify",
          "body": {"id_number": "{kwargs.id_number}"}
        }
      }
    }

Everything is validated when the tool is saved, so a broken
configuration is rejected by the admin API instead of failing when a
client's user calls it.
"""

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.connectors.http.templates import (
    TemplateSyntaxError,
    find_placeholders,
)

OPERATION_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Placeholders a configuration may use in each place. Credentials are
# allowed anywhere a provider could need them, but never in the URL
# path, where they would end up in access logs.
_PATH_SOURCES = {"kwargs", "args", "settings", "user_uuid", "client_id"}
# Header names that carry credentials. Their values must come from the
# secret store; the tool configuration itself is stored unencrypted.
_SENSITIVE_HEADER = re.compile(r"authorization|api[-_]?key|token|secret", re.I)


def _source_of(placeholder: str) -> str:
    return placeholder.split(".", 1)[0]


class ResponseMapping(BaseModel):
    """Where the provider puts its result in a JSON response.

    Attributes:
        success_field: Boolean field meaning "the provider succeeded".
            When absent or null, any 2xx status counts as success.
        data_field: Field holding the result payload. When null, the
            whole JSON body is returned as data.
        message_field: Field holding a human-readable message.
    """

    model_config = ConfigDict(extra="forbid")

    success_field: str | None = "success"
    data_field: str | None = "data"
    message_field: str | None = "message"


class OperationConfig(BaseModel):
    """One call the connector can make.

    Attributes:
        method: HTTP method.
        path: Path appended to the environment's base URL. May contain
            placeholders (values are URL-encoded), but not credentials.
        description: Shown to clients in the tool listing.
        query: Query parameters (templates).
        body: JSON body (template); omitted for GET and DELETE.
        response: Overrides the connector-wide response mapping.
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=500)
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | list[Any] | None = None
    response: ResponseMapping | None = None

    @field_validator("path")
    @classmethod
    def check_path(cls, path: str) -> str:
        """Keep operations on the configured host: relative paths only."""
        if not path.startswith("/") or "://" in path or path.startswith("//"):
            raise ValueError("path must be relative and start with '/'")
        return path

    def placeholders(self) -> set[str]:
        """Return every placeholder the operation reads."""
        return find_placeholders([self.path, self.query, self.body])


def _check_base_url(url: str, label: str) -> None:
    parts = urlsplit(url)
    is_local = parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and is_local):
        raise ValueError(f"{label} must use https")
    # A placeholder in the host would let a stored value choose where
    # credentials are sent.
    if not parts.hostname or "{" in parts.netloc:
        raise ValueError(f"{label} must have a fixed host")
    if parts.query or parts.fragment:
        raise ValueError(f"{label} must be a plain URL")


class OAuth2ClientCredentials(BaseModel):
    """OAuth 2.0 client credentials grant (RFC 6749 section 4.4).

    The connector requests a token with the client's id and secret,
    caches it until shortly before it expires, and sends it as
    ``Authorization: Bearer <token>`` on every operation. This is how
    service accounts for Microsoft Entra ID, Workday, and similar tools
    authenticate.

    Attributes:
        token_url: Token endpoint. The host must be literal; the path may
            use ``{credentials.*}`` or ``{settings.*}`` (for example an
            Entra tenant id).
        client_id: Usually ``{credentials.client_id}``.
        client_secret: Must be a ``{credentials.*}`` placeholder.
        scope: Space-separated scopes, if the provider needs them.
        audience: Audience parameter, for providers that use one.
        client_auth: Send the id and secret in the form body or with
            HTTP Basic authentication; providers differ.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth2_client_credentials"] = "oauth2_client_credentials"
    token_url: str = Field(max_length=500)
    client_id: str = "{credentials.client_id}"
    client_secret: str = "{credentials.client_secret}"  # noqa: S105 - placeholder
    scope: str | None = Field(default=None, max_length=1000)
    audience: str | None = Field(default=None, max_length=500)
    client_auth: Literal["body", "basic"] = "body"

    @model_validator(mode="after")
    def check_fields(self) -> "OAuth2ClientCredentials":
        """Keep the token endpoint fixed and the secret out of config."""
        _check_base_url(self.token_url, "token_url")
        try:
            url_sources = {
                _source_of(placeholder)
                for placeholder in find_placeholders(self.token_url)
            }
            id_sources = {
                _source_of(placeholder)
                for placeholder in find_placeholders(self.client_id)
            }
            secret_placeholders = find_placeholders(self.client_secret)
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        if not url_sources <= {"credentials", "settings"}:
            raise ValueError(
                "token_url may only use credentials or settings placeholders"
            )
        if not id_sources or not id_sources <= {"credentials", "settings"}:
            raise ValueError(
                "client_id must come from a credentials or settings "
                "placeholder"
            )
        if not re.fullmatch(
            r"\{credentials\.[A-Za-z0-9_]+\}", self.client_secret
        ):
            raise ValueError(
                "client_secret must be exactly one {credentials.<name>} "
                "placeholder"
            )
        if not secret_placeholders:
            raise ValueError("client_secret must use a placeholder")
        return self

    def placeholders(self) -> set[str]:
        """Return every placeholder the token request reads."""
        return find_placeholders(
            [self.token_url, self.client_id, self.client_secret]
        )


class HttpConnectorConfig(BaseModel):
    """Full configuration of a configuration-driven HTTP connector."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"
    environments: dict[str, str] = Field(min_length=1)
    default_environment: str
    auth: OAuth2ClientCredentials | None = Field(
        default=None,
        description=(
            "Token-based authentication. Leave empty when static headers "
            "(such as an API key header) are enough."
        ),
    )
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    response: ResponseMapping = Field(default_factory=ResponseMapping)
    operations: dict[str, OperationConfig] = Field(default_factory=dict)
    connect_operation: str | None = Field(
        default=None,
        description=(
            "Operation run by the connect endpoint, typically a cheap "
            "check that the credentials work. When unset, connecting "
            "only checks that every required input is available."
        ),
    )
    test_operation: str | None = Field(
        default=None,
        description=(
            "Operation run by Test connection for the client as a whole "
            "(no user). It may use credentials and settings but not "
            "user inputs. When unset, the test obtains an OAuth token if "
            "auth is configured, or checks that credentials are stored."
        ),
    )

    @field_validator("environments")
    @classmethod
    def check_environment_urls(
        cls, environments: dict[str, str]
    ) -> dict[str, str]:
        """Require HTTPS, except for local test servers."""
        for name, url in environments.items():
            _check_base_url(url, f"environment '{name}'")
            if "{" in url:
                raise ValueError(
                    f"environment '{name}' must be a plain base URL"
                )
        return {name: url.rstrip("/") for name, url in environments.items()}

    @field_validator("operations")
    @classmethod
    def check_operation_names(
        cls, operations: dict[str, OperationConfig]
    ) -> dict[str, OperationConfig]:
        """Operation names appear in URLs, so keep them simple."""
        for name in operations:
            if not re.fullmatch(OPERATION_NAME_PATTERN, name):
                raise ValueError(
                    f"operation name '{name}' must be lower_snake_case"
                )
        return operations

    @model_validator(mode="after")
    def check_references(self) -> "HttpConnectorConfig":
        """Check that names and placeholders all point somewhere real."""
        if self.default_environment not in self.environments:
            raise ValueError("default_environment must be in environments")
        for field_name in ("connect_operation", "test_operation"):
            operation_name = getattr(self, field_name)
            if (
                operation_name is not None
                and operation_name not in self.operations
            ):
                raise ValueError(f"{field_name} must name an operation")
        if self.test_operation is not None and any(
            _source_of(placeholder) in {"args", "kwargs", "user_uuid"}
            for placeholder in self.operations[
                self.test_operation
            ].placeholders()
        ):
            raise ValueError(
                "test_operation runs without a user, so it may not use "
                "args, kwargs, or user_uuid"
            )
        if self.auth is not None and any(
            name.lower() == "authorization" for name in self.headers
        ):
            raise ValueError(
                "auth sets the Authorization header; remove it from headers"
            )
        try:
            header_placeholders = find_placeholders(self.headers)
            for name, operation in self.operations.items():
                operation.placeholders()
                path_sources = {
                    _source_of(placeholder)
                    for placeholder in find_placeholders(operation.path)
                }
                if not path_sources <= _PATH_SOURCES:
                    raise ValueError(
                        f"operation '{name}' path may not use credentials"
                    )
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        if any(
            _source_of(placeholder) in {"args", "kwargs"}
            for placeholder in header_placeholders
        ):
            raise ValueError(
                "headers may use credentials and settings, not user inputs"
            )
        for header_name, header_value in self.headers.items():
            if _SENSITIVE_HEADER.search(header_name) and not any(
                _source_of(placeholder) == "credentials"
                for placeholder in find_placeholders(header_value)
            ):
                raise ValueError(
                    f"header '{header_name}' must take its value from a "
                    "{credentials.<name>} placeholder, not a literal"
                )
        return self

    def connection_placeholders(self) -> set[str]:
        """Return placeholders every call needs: headers and auth."""
        placeholders = find_placeholders(self.headers)
        if self.auth is not None:
            placeholders |= self.auth.placeholders()
        return placeholders

    def required_inputs(self, operation_name: str) -> set[str]:
        """Return placeholders an operation needs, auth included."""
        operation = self.operations[operation_name]
        return operation.placeholders() | self.connection_placeholders()

    def required_credentials(self) -> list[str]:
        """Return every credential name any part of the config reads."""
        placeholders = self.connection_placeholders()
        for operation in self.operations.values():
            placeholders |= operation.placeholders()
        return sorted(
            placeholder.split(".", 1)[1]
            for placeholder in placeholders
            if placeholder.startswith("credentials.")
        )

    @property
    def auth_method(self) -> str:
        """Short name of how the connector authenticates."""
        if self.auth is not None:
            return self.auth.type
        if self.headers:
            return "headers"
        return "none"

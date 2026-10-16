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
_PATH_SOURCES = {
    "kwargs",
    "args",
    "settings",
    "session",
    "user_uuid",
    "client_id",
}
_INPUT_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
# Dot path into a provider response, e.g. data.address.city or items.0.id.
_DATA_PATH_PATTERN = r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"
# Header names that carry credentials. Their values must come from the
# secret store; the tool configuration itself is stored unencrypted.
_SENSITIVE_HEADER = re.compile(r"authorization|api[-_]?key|token|secret", re.I)
# Grant types are plain words or URNs (RFC 6749 section 4.5).
_GRANT_TYPE_PATTERN = r"^[A-Za-z0-9_.:/-]{1,100}$"
_FORM_FIELD_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
# Token form fields that have their own auth setting.
_TOKEN_FORM_FIELDS = {
    "grant_type",
    "scope",
    "audience",
    "client_id",
    "client_secret",
}


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
        grant_type: Sent as ``grant_type``. Standard providers use
            ``client_credentials``; a few use their own value with the
            same token exchange (Keka uses ``kekaapi``).
        extra_form: Further form fields for the token request, such as
            Keka's ``{"api_key": "{credentials.api_key}"}``. Values are
            literals or ``{credentials.*}`` / ``{settings.*}``
            placeholders.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth2_client_credentials"] = "oauth2_client_credentials"
    token_url: str = Field(max_length=500)
    client_id: str = "{credentials.client_id}"
    client_secret: str = "{credentials.client_secret}"  # noqa: S105 - placeholder
    scope: str | None = Field(default=None, max_length=1000)
    audience: str | None = Field(default=None, max_length=500)
    client_auth: Literal["body", "basic"] = "body"
    grant_type: str = Field(
        default="client_credentials", pattern=_GRANT_TYPE_PATTERN
    )
    extra_form: dict[str, str] = Field(default_factory=dict, max_length=10)

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
            form_sources = {
                _source_of(placeholder)
                for placeholder in find_placeholders(self.extra_form)
            }
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        if reserved := _TOKEN_FORM_FIELDS & set(self.extra_form):
            raise ValueError(
                f"extra_form may not set '{sorted(reserved)[0]}'; use the "
                "matching auth field"
            )
        for name in self.extra_form:
            if not re.fullmatch(_FORM_FIELD_PATTERN, name):
                raise ValueError(f"extra_form name '{name}' is not valid")
        # The token request runs for the client as a whole, so it can
        # only read the client's stored credentials and settings.
        if not form_sources <= {"credentials", "settings"}:
            raise ValueError(
                "extra_form may only use credentials or settings placeholders"
            )
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
            [
                self.token_url,
                self.client_id,
                self.client_secret,
                self.extra_form,
            ]
        )


# Session value holding the URL a redirect step gives the provider: the
# client's redirect URI with this session's single-use ``state`` added.
RETURN_URL_VALUE = "return_url"


class StepRedirect(BaseModel):
    """Send the holder to a URL the step's response holds, then wait.

    For providers whose consent page is not an OpenID Connect sign-in
    (DigiLocker through SurePass, say): the step starts a session at the
    provider, which answers with a URL for the holder's browser. The
    flow pauses as ``awaiting_redirect`` until the client relays the
    holder's return, then runs the remaining steps.

    Attributes:
        url: Dot path to the URL in this step's response data.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=200)

    @field_validator("url")
    @classmethod
    def check_url_path(cls, url: str) -> str:
        """Keep the path a plain dot path, like other response paths."""
        if not re.fullmatch(_DATA_PATH_PATTERN, url):
            raise ValueError(f"redirect url path '{url}' is not a dot path")
        return url


class FlowStep(BaseModel):
    """One call inside a flow.

    Attributes:
        operation: Operation to run.
        inputs: Names the caller must have supplied before this step
            runs; they are passed as ``kwargs``. A step whose inputs are
            missing pauses the session until they arrive (e.g. an OTP).
        capture: Session values to keep for later steps, as
            ``name -> dot path`` into this step's response data (e.g.
            ``{"request_ref": "client_id"}``). Later steps read them as
            ``{session.request_ref}``.
        redirect: After this step, send the holder to the URL in its
            response and wait for them to return; see
            :class:`StepRedirect`. The step can pass the return URL to
            the provider as ``{session.return_url}``.
    """

    model_config = ConfigDict(extra="forbid")

    operation: str
    inputs: list[str] = Field(default_factory=list, max_length=20)
    capture: dict[str, str] = Field(default_factory=dict, max_length=20)
    redirect: StepRedirect | None = None

    @model_validator(mode="after")
    def check_names(self) -> "FlowStep":
        """Input and capture names become placeholders, so keep them tidy."""
        for name in [*self.inputs, *self.capture]:
            if not re.fullmatch(_INPUT_NAME_PATTERN, name):
                raise ValueError(f"'{name}' must be lower_snake_case")
        # Inputs are passed as run_operation(operation, **inputs).
        reserved = {"self", "operation"} & set(self.inputs)
        if reserved:
            raise ValueError(
                f"input name '{reserved.pop()}' is reserved; choose another"
            )
        for path in self.capture.values():
            if not re.fullmatch(_DATA_PATH_PATTERN, path):
                raise ValueError(f"capture path '{path}' is not a dot path")
        if RETURN_URL_VALUE in self.capture:
            raise ValueError(
                f"capture name '{RETURN_URL_VALUE}' is reserved; "
                "choose another"
            )
        return self


class FlowCondition(BaseModel):
    """A check on the final step's data that decides "verified".

    Attributes:
        path: Dot path into the final response data.
        equals: Required value, when set.
        one_of: Allowed values, when set.
        Neither set: the value must be present and truthy.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=_DATA_PATH_PATTERN)
    equals: Any = None
    one_of: list[Any] | None = Field(default=None, max_length=50)


class FlowInputSpec(BaseModel):
    """How a holder-facing screen should ask for one flow input.

    The client renders its form from these, so a new verification
    method needs no app release. The service also enforces ``pattern``
    and the length bounds before any provider is called.

    Attributes:
        label: Field label, e.g. "Aadhaar number".
        input_type: Keyboard and widget hint for the screen.
        pattern: Full-match regular expression the value must satisfy.
        min_length: Shortest accepted value.
        max_length: Longest accepted value.
        is_masked: Hide the value while typing, and when shown back.
        hint: Placeholder or helper text.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    input_type: Literal["text", "number", "otp", "date", "email", "phone"] = (
        "text"
    )
    pattern: str | None = Field(default=None, max_length=200)
    min_length: int | None = Field(default=None, ge=0, le=1000)
    max_length: int | None = Field(default=None, ge=1, le=1000)
    is_masked: bool = False
    hint: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def check_bounds(self) -> "FlowInputSpec":
        """Reject a pattern that does not compile and inverted bounds."""
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern is not valid: {exc}") from exc
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length must not exceed max_length")
        return self


class OAuth2AuthorizationCode(BaseModel):
    """OpenID Connect sign-in that opens a flow (authorization code + PKCE).

    Used by redirect-based identity providers such as Microsoft Entra ID.
    The session returns an authorization URL for the holder's browser,
    pauses as ``awaiting_redirect``, and resumes when the client relays
    the provider's ``code`` and ``state``. The code is exchanged here
    with the PKCE verifier, and the ID token's claims become the data
    later steps and ``outputs`` read. Refresh tokens are discarded
    immediately, and so is the access token unless ``steps_as_user``
    asks for it; nothing is stored beyond the claims.

    Attributes:
        authorize_url: Authorization endpoint. Fixed host; the path may
            use ``{credentials.*}`` or ``{settings.*}`` (an Entra tenant
            id, say). It is shown to the holder's browser, so it must
            never use the credential that holds the client secret.
        token_url: Token endpoint, under the same rules.
        client_id: Usually ``{credentials.client_id}``.
        client_secret: Exactly one ``{credentials.*}`` placeholder.
        scope: Must include ``openid`` so an ID token is returned.
        issuer: Expected ``iss`` claim; strongly recommended.
        client_auth: Send the secret in the form body or with HTTP Basic.
        extra_params: Literal parameters added to the authorize URL,
            e.g. ``{"prompt": "login"}`` to force a fresh sign-in.
        capture: Session values to keep from the claims, as
            ``name -> dot path``, for later steps.
        steps_as_user: Run the flow's steps with the signed-in user's
            access token instead of the tool's ``auth``, so the provider
            itself limits them to that user's data (Microsoft Graph
            ``/me`` with the delegated ``User.Read`` scope, say). The
            token is used by the steps that run right after sign-in and
            is never stored, so those steps may not wait for inputs.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth2_authorization_code"] = "oauth2_authorization_code"
    authorize_url: str = Field(max_length=500)
    token_url: str = Field(max_length=500)
    client_id: str = "{credentials.client_id}"
    client_secret: str = "{credentials.client_secret}"  # noqa: S105 - placeholder
    scope: str = Field(default="openid profile email", max_length=1000)
    issuer: str | None = Field(default=None, max_length=500)
    client_auth: Literal["body", "basic"] = "body"
    extra_params: dict[str, str] = Field(default_factory=dict, max_length=10)
    capture: dict[str, str] = Field(default_factory=dict, max_length=20)
    steps_as_user: bool = False

    @model_validator(mode="after")
    def check_fields(self) -> "OAuth2AuthorizationCode":
        """Keep endpoints fixed, the secret private, and OIDC switched on."""
        _check_base_url(self.authorize_url, "authorize_url")
        _check_base_url(self.token_url, "token_url")
        if "openid" not in self.scope.split():
            raise ValueError("scope must include 'openid'")
        if not re.fullmatch(
            r"\{credentials\.[A-Za-z0-9_]+\}", self.client_secret
        ):
            raise ValueError(
                "client_secret must be exactly one {credentials.<name>} "
                "placeholder"
            )
        try:
            public_placeholders = find_placeholders(
                [self.authorize_url, self.client_id, self.issuer or ""]
            )
            url_placeholders = find_placeholders(
                [self.authorize_url, self.token_url, self.issuer or ""]
            )
            literal_placeholders = find_placeholders(self.extra_params)
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        if not {_source_of(p) for p in url_placeholders} <= {
            "credentials",
            "settings",
        }:
            raise ValueError(
                "authorization URLs may only use credentials or settings "
                "placeholders"
            )
        # The authorize URL and client id travel through the holder's
        # browser; the secret must never be one of the values they use.
        if self.client_secret.strip("{}") in public_placeholders:
            raise ValueError(
                "the client secret may not appear in authorize_url, "
                "client_id, or issuer"
            )
        if literal_placeholders:
            raise ValueError("extra_params must be literal values")
        reserved = {
            "client_id",
            "redirect_uri",
            "response_type",
            "scope",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
        } & set(self.extra_params)
        if reserved:
            raise ValueError(
                f"extra_params may not set '{sorted(reserved)[0]}'"
            )
        for name, path in self.capture.items():
            if not re.fullmatch(_INPUT_NAME_PATTERN, name):
                raise ValueError(f"'{name}' must be lower_snake_case")
            if not re.fullmatch(_DATA_PATH_PATTERN, path):
                raise ValueError(f"capture path '{path}' is not a dot path")
        return self

    def placeholders(self) -> set[str]:
        """Return every placeholder the sign-in and code exchange read."""
        return find_placeholders(
            [
                self.authorize_url,
                self.token_url,
                self.client_id,
                self.client_secret,
                self.issuer or "",
            ]
        )


class FlowConfig(BaseModel):
    """A named verification or data-gathering procedure.

    Attributes:
        purpose: ``verification`` (Confirm: is this really the person?)
            or ``gather`` (fetch attributes from a system of record).
        description: Shown to clients in the tool listing.
        authorization: Optional sign-in with the provider before the
            steps run. A flow with only an authorization and no steps
            reads its outputs from the ID token claims.
        steps: Operations run in order; a step waits for its inputs.
        input_specs: How to ask the holder for each input, by name.
        outputs: Attributes to return, as ``attribute -> dot path`` into
            the final step's data, or ``session.<name>`` for a captured
            value. Clients can override these per tool.
        verified_when: For verification, conditions on the final data
            that must all hold. Without any, provider success is enough.
    """

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["verification", "gather"]
    description: str | None = Field(default=None, max_length=500)
    authorization: OAuth2AuthorizationCode | None = None
    steps: list[FlowStep] = Field(default_factory=list, max_length=10)
    input_specs: dict[str, FlowInputSpec] = Field(
        default_factory=dict, max_length=20
    )
    outputs: dict[str, str] = Field(default_factory=dict, max_length=100)
    verified_when: list[FlowCondition] = Field(
        default_factory=list, max_length=20
    )

    @property
    def kind(self) -> Literal["input", "redirect"]:
        """``redirect`` when the holder is sent to the provider's site."""
        if self.authorization or self.redirect_step_index is not None:
            return "redirect"
        return "input"

    @property
    def redirect_step_index(self) -> int | None:
        """Position of the step that sends the holder away, if any."""
        return next(
            (
                index
                for index, step in enumerate(self.steps)
                if step.redirect is not None
            ),
            None,
        )

    @model_validator(mode="after")
    def check_outputs(self) -> "FlowConfig":
        """Validate output paths and purpose-specific rules."""
        if not self.steps and self.authorization is None:
            raise ValueError("a flow needs steps, an authorization, or both")
        if unknown := set(self.input_specs) - set(self.all_inputs()):
            raise ValueError(
                "input_specs name inputs no step asks for: "
                + ", ".join(sorted(unknown))
            )
        for attribute, path in self.outputs.items():
            if not re.fullmatch(_INPUT_NAME_PATTERN, attribute):
                raise ValueError(
                    f"output attribute '{attribute}' must be lower_snake_case"
                )
            if not re.fullmatch(_DATA_PATH_PATTERN, path):
                raise ValueError(f"output path '{path}' is not a dot path")
        if self.purpose == "gather" and not self.outputs:
            raise ValueError("a gather flow must declare outputs")
        if self.purpose == "gather" and self.verified_when:
            raise ValueError("verified_when applies to verification flows")
        self._check_redirect_step()
        if self.authorization and self.authorization.steps_as_user:
            if not self.steps:
                raise ValueError("steps_as_user needs at least one step")
            if self.all_inputs():
                # The user's token is never stored, so a step that
                # pauses for inputs would resume without it.
                raise ValueError("steps_as_user flows may not take inputs")
        return self

    def _check_redirect_step(self) -> None:
        redirect_steps = [step for step in self.steps if step.redirect]
        if not redirect_steps:
            return
        if len(redirect_steps) > 1:
            raise ValueError("a flow may send the holder away only once")
        if self.authorization is not None:
            raise ValueError(
                "a flow uses either an authorization or a redirect step"
            )
        if self.steps[-1].redirect is not None:
            # The return carries nothing; a later step has to fetch the
            # result the holder agreed to share.
            raise ValueError("a redirect step needs a step after it")
        if self.all_inputs():
            # The holder acts on the provider's site, not in a form.
            raise ValueError("flows with a redirect step may not take inputs")

    def all_inputs(self) -> list[str]:
        """Every input any step asks for, in first-use order."""
        seen: dict[str, None] = {}
        for step in self.steps:
            for name in step.inputs:
                seen.setdefault(name, None)
        return list(seen)


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
    flows: dict[str, FlowConfig] = Field(
        default_factory=dict,
        description=(
            "Verification and gather procedures built from operations; "
            "run as sessions through /client/users/{user}/sessions."
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
            _source_of(placeholder) in {"args", "kwargs", "session"}
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

    @model_validator(mode="after")
    def check_flows(self) -> "HttpConnectorConfig":
        """Check each flow step against the operations it runs.

        Every ``{kwargs.x}`` an operation reads must be declared as an
        input by that step or an earlier one, and every ``{session.x}``
        must be captured by an earlier step, so a session can never get
        stuck on a value nobody is asked for.
        """
        for flow_name, flow in self.flows.items():
            if not re.fullmatch(OPERATION_NAME_PATTERN, flow_name):
                raise ValueError(
                    f"flow name '{flow_name}' must be lower_snake_case"
                )
            declared_inputs: set[str] = set()
            captured: set[str] = (
                set(flow.authorization.capture)
                if flow.authorization
                else set()
            )
            if flow.redirect_step_index is not None:
                captured.add(RETURN_URL_VALUE)
            for position, step in enumerate(flow.steps, start=1):
                operation = self.operations.get(step.operation)
                if operation is None:
                    raise ValueError(
                        f"flow '{flow_name}' step {position} names unknown "
                        f"operation '{step.operation}'"
                    )
                declared_inputs.update(step.inputs)
                placeholders = operation.placeholders()
                needed_inputs = {
                    p.split(".", 1)[1]
                    for p in placeholders
                    if p.startswith("kwargs.")
                }
                needed_session = {
                    p.split(".", 1)[1]
                    for p in placeholders
                    if p.startswith("session.")
                }
                if missing := needed_inputs - declared_inputs:
                    raise ValueError(
                        f"flow '{flow_name}' step {position} uses inputs "
                        f"no step asks for: {', '.join(sorted(missing))}"
                    )
                if missing := needed_session - captured:
                    raise ValueError(
                        f"flow '{flow_name}' step {position} uses session "
                        "values no earlier step captures: "
                        + ", ".join(sorted(missing))
                    )
                if any(p.startswith("args.") for p in placeholders):
                    raise ValueError(
                        f"flow '{flow_name}' step {position}: flows pass "
                        "named inputs only; use {kwargs.<name>}"
                    )
                captured.update(step.capture)
            for path in flow.outputs.values():
                if path.startswith("session.") and (
                    path.removeprefix("session.") not in captured
                ):
                    raise ValueError(
                        f"flow '{flow_name}' output '{path}' is never captured"
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
        for flow in self.flows.values():
            if flow.authorization is not None:
                placeholders |= flow.authorization.placeholders()
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

"""Contract every integration connector implements.

A connector is the code behind one integration tool (a row in
``integration_tools``). It links one client user to that tool and is
called with the positional and keyword arguments stored for the user,
exactly as ``connector.connect(*args, **kwargs)``, so each connector
declares the parameters it needs in its own signature.
"""

import inspect
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx


class ConnectorError(Exception):
    """Raised by a connector when the external tool refuses or fails.

    The message is stored on the connection and returned to the client,
    so it must describe the problem without including credentials or
    raw provider responses.
    """


class UnknownOperationError(Exception):
    """Raised when a connector does not offer the requested operation."""


class MissingParametersError(Exception):
    """Raised when an operation needs inputs that were not supplied.

    Attributes:
        missing: Names such as ``kwargs.id_number`` or
            ``credentials.api_token``; never values.
    """

    def __init__(self, missing: list[str]) -> None:
        super().__init__(", ".join(missing))
        self.missing = missing


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    """What a connector knows about the call besides its arguments.

    Attributes:
        client_id: Client project the user belongs to.
        user_uuid: The client's identifier for the user, or None for
            client-level calls such as Test connection.
        integration_type_code: Type the user is being connected for;
            useful when one tool serves several types.
        tool_code: Code of the tool this connector implements.
        client_settings: The client's non-secret settings for this
            integration type (from ``client_integration_configs``).
        credentials: The client's credentials for this tool, read from
            the secret store for this call only. Excluded from ``repr``
            so the context can never leak them into logs.
        tool_config: The tool's ``connector_config`` from the database,
            used by configuration-driven connectors.
        http_client: Shared outbound HTTP client with timeouts applied.
    """

    client_id: uuid.UUID
    user_uuid: uuid.UUID | None
    integration_type_code: str
    tool_code: str
    client_settings: dict[str, Any] = field(repr=False)
    http_client: httpx.AsyncClient = field(repr=False)
    credentials: dict[str, Any] = field(default_factory=dict, repr=False)
    tool_config: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    """Result of running one operation against the external tool.

    A provider saying "no" (an invalid document number, say) is a
    normal outcome with ``success=False``; transport failures raise
    :class:`ConnectorError` instead.

    Attributes:
        success: Whether the provider reported success.
        status_code: HTTP status the provider returned, if any.
        data: The provider's result payload, returned to the caller.
        message: The provider's human-readable message, if any.
    """

    success: bool
    status_code: int | None = None
    data: Any = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionTestOutcome:
    """Result of a successful client-level connection test.

    Attributes:
        called_provider: False when the connector could only confirm
            that credentials are stored, without calling the provider.
        message: Human-readable summary shown to the admin.
    """

    called_provider: bool
    message: str


@dataclass(frozen=True, slots=True)
class ConnectionRequirements:
    """What a client must provide before a tool can be used.

    Attributes:
        auth_method: ``oauth2_client_credentials``, ``headers``,
            ``none``, or ``custom`` for Python connectors.
        required_credentials: Credential names the connector reads.
        can_test: Whether Test connection reaches the provider.
    """

    auth_method: str
    required_credentials: list[str]
    can_test: bool


@dataclass(frozen=True, slots=True)
class OperationDescription:
    """What an operation needs, for the tool listing.

    Attributes:
        name: Operation name used in the API path.
        description: What the operation does.
        required_kwargs: ``kwargs`` names the operation reads.
        required_credentials: Credential names it reads.
    """

    name: str
    description: str | None
    required_kwargs: list[str]
    required_credentials: list[str]


@dataclass(frozen=True, slots=True)
class ConnectionOutcome:
    """Result of a successful connection.

    Attributes:
        details: Non-secret facts worth keeping, such as the provider's
            account reference. Stored in plain JSON and returned to the
            client, so never include tokens here.
    """

    details: dict[str, Any] = field(default_factory=dict)


class IntegrationConnector(ABC):
    """Base class for connectors to external integration tools.

    Subclasses implement :meth:`connect` with explicit parameters, for
    example ``async def connect(self, api_token: str, *, region: str)``.
    The declared signature is what stored ``args`` and ``kwargs`` are
    checked against before the connector is ever called.
    """

    def __init__(self, context: ConnectorContext) -> None:
        self.context = context

    async def run_operation(
        self, operation: str, *args: Any, **kwargs: Any
    ) -> OperationOutcome:
        """Run a named operation, such as a document verification.

        Connectors that offer operations override this. The default
        offers none.

        Raises:
            UnknownOperationError: The connector has no such operation.
            MissingParametersError: Required inputs were not supplied.
            ConnectorError: The call to the tool failed.
        """
        raise UnknownOperationError(operation)

    async def test_connection(self) -> ConnectionTestOutcome:
        """Check the client's credentials without any user.

        Connectors that can test themselves override this.

        Raises:
            UnknownOperationError: The connector cannot be tested.
            MissingParametersError: A credential is missing.
            ConnectorError: The provider refused or was unreachable.
        """
        raise UnknownOperationError("test_connection")

    @classmethod
    def describe_requirements(
        cls, tool_config: dict[str, Any] | None
    ) -> ConnectionRequirements:
        """Describe authentication and credentials for the admin UI."""
        return ConnectionRequirements(
            auth_method="custom", required_credentials=[], can_test=False
        )

    @classmethod
    def describe_operations(
        cls, tool_config: dict[str, Any] | None
    ) -> list[OperationDescription]:
        """List the operations this connector offers for ``tool_config``."""
        return []

    @abstractmethod
    async def connect(self, *args: Any, **kwargs: Any) -> ConnectionOutcome:
        """Connect the user to the external tool.

        Raises:
            ConnectorError: The tool rejected the connection.
        """


@dataclass(frozen=True, slots=True)
class ConnectorParameter:
    """One parameter a connector's ``connect`` accepts.

    Attributes:
        name: Parameter name, used as the ``kwargs`` key.
        kind: ``positional_or_keyword``, ``positional_only``,
            ``keyword_only``, ``var_positional``, or ``var_keyword``.
        required: False when the parameter has a default or collects
            extra arguments (``*args`` / ``**kwargs``).
        annotation: The declared type's name, if any.
    """

    name: str
    kind: str
    required: bool
    annotation: str | None


_VARIADIC_KINDS = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
)


def describe_parameters(
    connector_class: type[IntegrationConnector],
) -> list[ConnectorParameter]:
    """List what ``connector_class.connect`` accepts, excluding ``self``.

    Default values are deliberately left out; a connector may use a
    default that should not be published.
    """
    signature = inspect.signature(connector_class.connect)
    described: list[ConnectorParameter] = []
    for parameter in list(signature.parameters.values())[1:]:
        annotation = parameter.annotation
        annotation_name = (
            None
            if annotation is inspect.Parameter.empty
            else getattr(annotation, "__name__", str(annotation))
        )
        described.append(
            ConnectorParameter(
                name=parameter.name,
                kind=parameter.kind.name.lower(),
                required=parameter.default is inspect.Parameter.empty
                and parameter.kind not in _VARIADIC_KINDS,
                annotation=annotation_name,
            )
        )
    return described


def check_arguments_fit(
    connector_class: type[IntegrationConnector],
    args: list[Any],
    kwargs: dict[str, Any],
) -> str | None:
    """Return why ``args``/``kwargs`` do not fit ``connect``, or None.

    Binding against the signature catches missing, extra, and duplicate
    arguments up front, instead of as a ``TypeError`` halfway through a
    connection attempt.
    """
    signature = inspect.signature(connector_class.connect)
    try:
        # ``None`` stands in for ``self``; only the shape is checked.
        signature.bind(None, *args, **kwargs)
    except TypeError as exc:
        return str(exc)
    return None

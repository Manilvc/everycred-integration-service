"""Fixtures for session tests: a fake provider and a configured client."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.http_client import get_http_client
from app.features.integration_types.models import IntegrationType
from tests.features.clients.conftest import create_client, issue_api_key

USER = "44444444-4444-4444-8444-444444444444"
API_TOKEN = "idv-token-do-not-leak"
AADHAAR = "999988887777"
OTP = "123456"

# A provider with an OTP-based identity check (two steps) and an HR
# lookup (one step), described the way a super admin would save it.
TOOL_DEFINITION: dict[str, Any] = {
    "name": "Acme Identity",
    "provider": "Acme",
    "integration_types": ["confirm", "gather"],
    "connector_config": {
        "type": "http",
        "environments": {"sandbox": "http://127.0.0.1:9/api"},
        "default_environment": "sandbox",
        "headers": {"Authorization": "Bearer {credentials.api_token}"},
        "operations": {
            "generate_otp": {
                "path": "/otp/generate",
                "body": {"id_number": "{kwargs.id_number}"},
            },
            "submit_otp": {
                "path": "/otp/submit",
                "body": {
                    "request_id": "{session.request_ref}",
                    "otp": "{kwargs.otp}",
                },
            },
            "fetch_employee": {
                "method": "GET",
                "path": "/employees/{kwargs.employee_id}",
            },
        },
        "flows": {
            "aadhaar_otp": {
                "purpose": "verification",
                "description": "Aadhaar number, then the OTP sent to it",
                "steps": [
                    {
                        "operation": "generate_otp",
                        "inputs": ["id_number"],
                        "capture": {"request_ref": "request_id"},
                    },
                    {"operation": "submit_otp", "inputs": ["otp"]},
                ],
                "outputs": {
                    "full_name": "full_name",
                    "date_of_birth": "dob",
                    "provider_reference": "session.request_ref",
                },
                "verified_when": [{"path": "status", "equals": "valid"}],
            },
            "employee_profile": {
                "purpose": "gather",
                "steps": [
                    {"operation": "fetch_employee", "inputs": ["employee_id"]}
                ],
                "outputs": {
                    "email": "work_email",
                    "department": "org.department",
                },
            },
        },
    },
}


def default_reply(request: httpx.Request) -> httpx.Response:
    """Answer like a healthy provider."""
    path = request.url.path
    if path.endswith("/otp/generate"):
        return httpx.Response(
            200, json={"success": True, "data": {"request_id": "req-42"}}
        )
    if path.endswith("/otp/submit"):
        body = json.loads(request.content)
        valid = body.get("otp") == OTP and body.get("request_id") == "req-42"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "status": "valid" if valid else "invalid",
                    "full_name": "Asha Verma",
                    "dob": "1990-01-01",
                },
            },
        )
    if "/employees/" in path:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "work_email": "asha@example.com",
                    "org": {"department": "Finance"},
                    "employee_code": "E-1001",
                },
            },
        )
    # Webhook deliveries and anything else.
    return httpx.Response(204)


class FakeProvider:
    """Records requests and answers them; tests may replace ``reply``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: Callable[[httpx.Request], httpx.Response] = default_reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.reply(request)

    def paths(self) -> list[str]:
        """Paths called so far, in order."""
        return [request.url.path for request in self.requests]


@pytest.fixture
def provider(app: FastAPI) -> FakeProvider:
    fake = FakeProvider()
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    app.dependency_overrides[get_http_client] = lambda: mock_client
    fake.http_client = mock_client  # type: ignore[attr-defined]
    return fake


@pytest.fixture
async def setup(
    client: AsyncClient,
    super_admin_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    provider: FakeProvider,
) -> dict[str, Any]:
    """A client with Confirm and Gather served by the Acme tool."""
    async with session_factory() as session:
        session.add_all(
            [
                IntegrationType(code="confirm", name="Confirm"),
                IntegrationType(code="gather", name="Gather"),
            ]
        )
        await session.commit()
    tool = await client.put(
        "/api/v1/integration-tools/acme-identity",
        json=TOOL_DEFINITION,
        headers=super_admin_headers,
    )
    assert tool.status_code == 200, tool.text
    portal = await create_client(client, super_admin_headers)
    for code in ("confirm", "gather"):
        config = await client.put(
            f"/api/v1/clients/{portal['id']}/integrations/{code}",
            json={"tool_code": "acme-identity"},
            headers=super_admin_headers,
        )
        assert config.status_code == 200, config.text
    credentials = await client.put(
        f"/api/v1/clients/{portal['id']}/tools/acme-identity/credentials",
        json={"credentials": {"api_token": API_TOKEN}},
        headers=super_admin_headers,
    )
    assert credentials.status_code == 200, credentials.text
    key = await issue_api_key(client, super_admin_headers, portal["id"])
    return {
        "client_id": portal["id"],
        "key": {"X-API-Key": key["api_key"]},
        "admin": super_admin_headers,
    }


def sessions_url(user: str = USER) -> str:
    return f"/api/v1/client/users/{user}/sessions"


def session_url(session_id: str, action: str = "") -> str:
    base = f"/api/v1/client/sessions/{session_id}"
    return f"{base}/{action}" if action else base


async def start(
    client: AsyncClient,
    setup: dict[str, Any],
    flow: str = "aadhaar_otp",
    integration_type: str = "confirm",
    **inputs: Any,
) -> httpx.Response:
    return await client.post(
        sessions_url(),
        json={
            "integration_type": integration_type,
            "flow": flow,
            "reference": "invite-7",
            "inputs": inputs,
        },
        headers=setup["key"],
    )

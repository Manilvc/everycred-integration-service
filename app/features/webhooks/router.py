"""Routes for a client to manage its webhook endpoint."""

from fastapi import APIRouter, Depends, status

from app.features.clients.dependencies import (
    CurrentClient,
    get_current_client,
)
from app.features.webhooks.dependencies import WebhookServiceDep
from app.features.webhooks.schemas import (
    WebhookResponse,
    WebhookTestResponse,
    WebhookUpdate,
)
from app.shared.schemas import ErrorResponse

router = APIRouter(
    prefix="/client/webhook",
    tags=["Webhooks"],
    dependencies=[Depends(get_current_client)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Missing or invalid API key",
        },
    },
)
_NOT_CONFIGURED = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "No webhook endpoint configured",
    }
}


@router.get(
    "",
    response_model=WebhookResponse,
    summary="Get the webhook endpoint",
    responses=_NOT_CONFIGURED,
)
async def get_webhook(
    current_client: CurrentClient, service: WebhookServiceDep
) -> WebhookResponse:
    """Return the configured endpoint. The signing secret is not shown."""
    return await service.get(current_client)


@router.put(
    "",
    response_model=WebhookResponse,
    summary="Set the webhook endpoint",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "URL not HTTPS or points to a private address",
        }
    },
)
async def set_webhook(
    update: WebhookUpdate,
    current_client: CurrentClient,
    service: WebhookServiceDep,
) -> WebhookResponse:
    """Create or change where session events are sent.

    The first call, and any call with `rotate_secret: true`, returns
    `signing_secret` **once**. Use it to verify the
    `X-EveryCRED-Signature` header on every event.
    """
    return await service.configure(current_client, update)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove the webhook endpoint",
    responses=_NOT_CONFIGURED,
)
async def delete_webhook(
    current_client: CurrentClient, service: WebhookServiceDep
) -> None:
    """Stop sending events and delete the signing secret."""
    await service.delete(current_client)


@router.post(
    "/test",
    response_model=WebhookTestResponse,
    summary="Send a test event",
    responses=_NOT_CONFIGURED,
)
async def test_webhook(
    current_client: CurrentClient, service: WebhookServiceDep
) -> WebhookTestResponse:
    """Send a signed `webhook.test` event now and report the result."""
    return await service.send_test(current_client)

"""Request and response models for client webhooks."""

from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.features.webhooks.models import WEBHOOK_URL_MAX_LENGTH


class WebhookUpdate(BaseModel):
    """Where to send events, and whether to rotate the signing secret."""

    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl = Field(
        description=(
            "HTTPS endpoint in your backend. Private and loopback "
            "addresses are refused unless the service allows them."
        ),
    )
    is_active: bool = True
    rotate_secret: bool = Field(
        default=False,
        description="Issue a new signing secret; the old one stops working.",
    )

    def url_text(self) -> str:
        """The URL as a plain string, length-checked."""
        text = str(self.url)
        if len(text) > WEBHOOK_URL_MAX_LENGTH:
            raise ValueError("url is too long")
        return text


class WebhookResponse(BaseModel):
    """The client's webhook endpoint.

    ``signing_secret`` is present only when a secret was just created or
    rotated; it cannot be read again.
    """

    url: str
    is_active: bool
    signing_secret: str | None = None
    created_at: datetime
    updated_at: datetime


class WebhookTestResponse(BaseModel):
    """Result of sending a test event right now."""

    delivered: bool
    status_code: int | None
    error: str | None

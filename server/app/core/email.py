"""Outgoing email behind a small interface.

Implementations: console (development: writes messages to the log) and disabled
(sends nothing). Production refuses the console backend (see Settings), because the
log would then hold working activation and reset links. A real backend (e.g. SMTP or an
email API) plugs in by implementing EmailSender and adding it to create_email_sender().
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings

logger: logging.Logger = logging.getLogger("app.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailDeliveryError(Exception):
    """The message could not be handed to the email service."""


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Deliver the message or raise EmailDeliveryError."""
        ...


class ConsoleEmailSender:
    """Development only: prints each message to the server log."""

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "\n--- email (console backend) ---\nTo: %s\nSubject: %s\n\n%s\n--- end email ---",
            message.to,
            message.subject,
            message.body,
        )


class DisabledEmailSender:
    """Sends nothing. Admin invites and resets fall back to showing the link once;
    forgot-password requests send nothing (the response stays the same)."""

    async def send(self, message: EmailMessage) -> None:
        raise EmailDeliveryError("Email is disabled (EMAIL_BACKEND=disabled)")


def create_email_sender(settings: Settings) -> EmailSender:
    match settings.email_backend:
        case "console":
            return ConsoleEmailSender()
        case "disabled":
            return DisabledEmailSender()

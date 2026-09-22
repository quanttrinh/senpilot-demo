"""Generic SMTP delivery via Zoho."""

import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

from regulatory_agent.config import EmailConfig

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
SMTP_TIMEOUT_SECONDS = 60

ATTACHMENT_TOO_LARGE_NOTICE = (
    "\n\nWarning: the requested archive is too large to send "
    f"(limit {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB) and was not attached."
)


class EmailSmtpSender:
    """Builds and delivers plain email messages over SMTP.

    Knows nothing about the application's domain: callers pass the
    recipient, subject, body, and optional attachment.
    """

    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachment_path: str = "",
        in_reply_to: str = "",
    ) -> None:
        """Compose and send an email, optionally replying and attaching a file."""
        attachment, notice = self._resolve_attachment(attachment_path)
        if notice:
            body = f"{body}{notice}"
        logger.debug(
            "preparing email to=%s subject=%r attachment=%s",
            to,
            subject,
            attachment.name if attachment else "none",
        )
        message = self._build_message(
            to=to,
            subject=subject,
            body=body,
            attachment=attachment,
            in_reply_to=in_reply_to,
        )
        self._send(message)
        logger.info("email sent to %s", to)

    def _resolve_attachment(self, attachment_path: str) -> tuple[Path | None, str]:
        """Return the attachment to send plus any body notice to append."""
        if not attachment_path:
            return None, ""
        path = Path(attachment_path)
        if not path.exists():
            logger.warning("attachment not found: %s", attachment_path)
            return None, ""
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            logger.error(
                "attachment %s (%d bytes) exceeds %d-byte limit; "
                "sending notice instead",
                path.name,
                size,
                MAX_ATTACHMENT_BYTES,
            )
            return None, ATTACHMENT_TOO_LARGE_NOTICE
        return path, ""

    def _build_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachment: Path | None,
        in_reply_to: str = "",
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.username
        message["To"] = to
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)

        if attachment is not None:
            size = attachment.stat().st_size
            maintype, subtype = self._content_type(attachment)
            logger.debug(
                "attaching %s (%d bytes, %s/%s)",
                attachment.name,
                size,
                maintype,
                subtype,
            )
            message.add_attachment(
                attachment.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment.name,
            )

        return message

    @staticmethod
    def _content_type(path: Path) -> tuple[str, str]:
        guessed = mimetypes.guess_type(path.name)[0]
        if guessed and "/" in guessed:
            maintype, _, subtype = guessed.partition("/")
            return maintype, subtype
        return "application", "octet-stream"

    def _send(self, message: EmailMessage) -> None:
        logger.debug(
            "connecting to smtp %s:%s", self.config.smtp_host, self.config.smtp_port
        )
        with smtplib.SMTP(
            self.config.smtp_host, self.config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        ) as smtp:
            smtp.starttls()
            smtp.login(self.config.username, self.config.password)
            smtp.send_message(message)
        logger.debug("smtp delivery complete")

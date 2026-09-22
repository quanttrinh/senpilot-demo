"""Generic IMAP IDLE listener."""

import logging
import time
from collections.abc import Callable

from imapclient import IMAPClient

from regulatory_agent.config import EmailConfig

logger = logging.getLogger(__name__)

IDLE_SLICE_SECONDS = 10


class EmailImapListener:
    """Watches an IMAP folder via IDLE and yields raw message bytes.

    Implements reconnect-with-backoff and re-issues IDLE every
    ``idle_timeout_min`` minutes to respect the RFC 2177 29-minute limit.
    Knows nothing about the application's domain: it hands each new message's
    raw bytes to the caller-supplied callback.
    """

    def __init__(self, config: EmailConfig) -> None:
        self.config = config
        self._client: IMAPClient | None = None

    def connect(self) -> None:
        """Connect, authenticate, and select the filtered folder."""
        self._client = IMAPClient(
            self.config.imap_host,
            port=self.config.imap_port,
            ssl=True,
        )
        self._client.login(self.config.username, self.config.password)
        self._client.select_folder(self.config.inbox_folder)
        logger.info(
            "connected to %s folder=%s", self.config.imap_host, self.config.inbox_folder
        )

    def disconnect(self) -> None:
        if self._client is not None:
            logger.debug("disconnecting from %s", self.config.imap_host)
            try:
                self._client.logout()
            except Exception:  # noqa: BLE001 - force-close if logout fails
                logger.debug("logout failed; forcing socket shutdown", exc_info=True)
                try:
                    self._client.shutdown()
                except Exception:  # noqa: BLE001 - best effort
                    logger.debug("socket shutdown failed", exc_info=True)
            self._client = None

    def idle_loop(
        self,
        on_message: Callable[[bytes], None],
        on_beat: Callable[[], None] | None = None,
    ) -> None:
        """Blocking IDLE loop; invokes ``on_message(raw_bytes)`` per new email.

        Reconnects with exponential backoff on any failure. ``on_beat`` is
        called periodically so a supervisor (e.g. the systemd watchdog) can tell
        the process is still alive.
        """
        backoff = 1.0
        while True:
            try:
                if on_beat is not None:
                    on_beat()
                if self._client is None:
                    self.connect()
                self._process_unseen(on_message)
                self._idle_once(
                    timeout=self.config.idle_timeout_min * 60, on_beat=on_beat
                )
                backoff = 1.0
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("listener error; reconnecting in %.1fs", backoff)
                self.disconnect()
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _idle_once(
        self, *, timeout: int, on_beat: Callable[[], None] | None = None
    ) -> None:
        """Enter IDLE for a single slice, then exit so the caller can drain mail.

        Sessions are kept short (``IDLE_SLICE_SECONDS``) and re-issued by the
        outer loop, so ``on_beat`` fires regularly and new mail is noticed
        promptly instead of waiting out the whole ``timeout`` window. We cannot
        gate on the value from ``idle_check`` (it may be empty even when mail
        arrived), so we always return and let the caller search ``UNSEEN``.
        """
        client = self._connection()
        client.idle()
        try:
            if on_beat is not None:
                on_beat()
            client.idle_check(timeout=min(timeout, IDLE_SLICE_SECONDS))
        finally:
            client.idle_done()

    def _process_unseen(self, on_message: Callable[[bytes], None]) -> None:
        client = self._connection()
        msg_ids = client.search(["UNSEEN"])
        if msg_ids:
            logger.info("found %d unseen message(s)", len(msg_ids))
        for msg_id in msg_ids:
            try:
                raw = self._fetch_raw(msg_id)
            except Exception:  # noqa: BLE001 - skip bad messages
                logger.exception("failed to fetch message %s", msg_id)
                continue
            try:
                on_message(raw)
            except Exception:  # noqa: BLE001 - never re-process a bad message
                logger.exception("handler failed for message %s", msg_id)
            finally:
                client.set_flags([msg_id], [b"\\Seen"])
                logger.debug("handled and marked message %s seen", msg_id)

    def _connection(self) -> IMAPClient:
        if self._client is None:
            raise RuntimeError("not connected to the IMAP server")
        return self._client

    def _fetch_raw(self, msg_id) -> bytes:
        """Fetch a message by id and return its raw RFC 822 bytes."""
        data = self._connection().fetch([msg_id], ["BODY[]"])
        raw = data[msg_id][b"BODY[]"]
        logger.debug("fetched message %s (%d bytes)", msg_id, len(raw))
        return raw

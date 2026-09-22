"""Minimal systemd sd_notify support (no external dependencies)."""

import logging
import os
import socket

logger = logging.getLogger(__name__)


def notify(state: str) -> None:
    """Send ``state`` to the systemd notify socket when running under systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode("utf-8"))
    except OSError:
        logger.debug("sd_notify failed for %r", state, exc_info=True)


def ready() -> None:
    """Signal that startup is complete."""
    notify("READY=1")


def stopping() -> None:
    """Signal that the service is shutting down."""
    notify("STOPPING=1")


def watchdog() -> None:
    """Feed the systemd watchdog; a stale loop stops feeding it and gets restarted."""
    notify("WATCHDOG=1")

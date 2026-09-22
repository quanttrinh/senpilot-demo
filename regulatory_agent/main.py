"""Entrypoint: orchestrates the full request -> scrape -> zip -> reply pipeline."""

import logging
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Protocol

from regulatory_agent.browser.scraper import Scraper
from regulatory_agent.browser.uarb_scraper import UarbScraper
from regulatory_agent.config import load_config, require_credentials
from regulatory_agent.email.listener import EmailImapListener
from regulatory_agent.email.sender import EmailSmtpSender
from regulatory_agent.email.uarb_request_reader import UarbRequestReader
from regulatory_agent.email.uarb_request_responder import RequestResponder
from regulatory_agent.llm import OpenCodeLlmClient
from regulatory_agent.models.uarb_request import DocumentRequest, ScrapeResult
from regulatory_agent.utils import systemd
from regulatory_agent.utils.file import get_safe_filename, zip_files

logger = logging.getLogger(__name__)


class ReplySender(Protocol):
    """Anything that can deliver the outcome of handling a request."""

    def send_reply(
        self,
        request: DocumentRequest,
        *,
        result: ScrapeResult | None = None,
        attachment_path: str = "",
        error: str | None = None,
    ) -> None: ...


def handle_request(
    request: DocumentRequest,
    *,
    scraper: Scraper[DocumentRequest, ScrapeResult],
    sender: ReplySender,
) -> None:
    """Process a single parsed request: do the work, then reply with the result."""
    if not request.valid:
        logger.warning(
            "invalid request from %s: %s", request.from_address, request.error
        )
        sender.send_reply(request, error=request.error or "invalid request")
        return

    logger.info(
        "valid request: matter=%s doc_types=%s from=%s",
        request.matter_number,
        request.doc_types,
        request.from_address,
    )
    result = None
    try:
        result = scraper.scrape(request)
        logger.info(
            "scraped %d document(s) for matter=%s",
            len(result.documents),
            request.matter_number,
        )
        document_paths = [document.file_path for document in result.documents]
        doc_type = "+".join(request.doc_types) or "documents"
        with tempfile.TemporaryDirectory(prefix="uarb_archive_") as archive_dir:
            archive_name = (
                f"{get_safe_filename(request.matter_number)}_"
                f"{get_safe_filename(doc_type)}.zip"
            )
            archive_path = zip_files(document_paths, Path(archive_dir) / archive_name)
            logger.info(
                "built archive %s for matter=%s",
                archive_path.name,
                request.matter_number,
            )
            sender.send_reply(request, result=result, attachment_path=str(archive_path))
            logger.info("reply sent for matter=%s", request.matter_number)
    except Exception:  # noqa: BLE001 - keep the listener alive for later mail
        logger.exception("failed to process matter %s", request.matter_number)
        try:
            sender.send_reply(
                request,
                error="an internal error occurred while retrieving documents",
            )
        except Exception:  # noqa: BLE001 - best effort error reply
            logger.exception(
                "failed to send error reply for matter=%s", request.matter_number
            )
        raise
    finally:
        if result and result.download_dir:
            shutil.rmtree(result.download_dir, ignore_errors=True)
            logger.debug("removed download dir %s", result.download_dir)


def main() -> None:
    """Start the IMAP IDLE listener and process incoming requests."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    require_credentials(config)
    llm = OpenCodeLlmClient(config.llm.base_url, config.llm.model, config.llm.api_key)
    reader = UarbRequestReader(llm)
    listener = EmailImapListener(config.email)
    scraper = UarbScraper(config.browser)
    responder = RequestResponder(EmailSmtpSender(config.email), llm)
    logger.info(
        "starting IMAP IDLE listener on %s:%s",
        config.email.imap_host,
        config.email.imap_port,
    )

    def on_message(raw: bytes) -> None:
        handle_request(reader.to_request(raw), scraper=scraper, sender=responder)

    signal.signal(signal.SIGTERM, _exit_on_signal)
    systemd.ready()
    try:
        listener.idle_loop(on_message, on_beat=systemd.watchdog)
    finally:
        systemd.stopping()


def _exit_on_signal(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


if __name__ == "__main__":
    main()

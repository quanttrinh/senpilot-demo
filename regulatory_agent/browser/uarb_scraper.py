"""Playwright browser automation against the UARB FileMaker WebDirect DB."""

import logging
import shutil
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Download, Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from regulatory_agent.browser.scraper import Scraper
from regulatory_agent.config import BrowserConfig
from regulatory_agent.constants import DOC_TYPES, DocumentType
from regulatory_agent.models.uarb_request import (
    Document,
    DocumentRequest,
    MatterMetadata,
    ScrapeResult,
)
from regulatory_agent.utils.file import get_safe_filename

logger = logging.getLogger(__name__)

SEARCH_TIMEOUT_MS = 15_000
SCROLL_TIMEOUT_MS = 3_000

MATTER_FIELD_SELECTORS = (
    "div.fm_object_254:has-text('eg M01234')",
    "div.fm-textarea:has-text('eg M01234')",
)

BACK_TO_RESULTS_SELECTORS = ("button:has-text('Back to Search Results')",)

DOC_TABS = tuple(doc_type.value for doc_type in DOC_TYPES)
EXHIBITS = DocumentType.EXHIBITS.value

TAB_BAR = "div.csFM-AE11ABB7-1031-A04A-9A0F-ABB8C87B8079-button-bar"

ROW = "tr.v-grid-row.v-grid-row-has-data"
IDENTIFIER_FIELD_SELECTORS = (
    "div.csFM-FC034851-2EE7-2849-AD28-0DF51F69AB41",
    "div.csFM-F162A04A-F4C1-C845-9CAB-27DDC948EAD6",
    "div.fm-textarea.hand-cursor",
)
TITLE_FIELD = "div.csminimal_edit_box"
GO_GET_IT_BUTTON = (
    "div.csFM-25B7C521-2598-BB4F-83FE-96AEAF6609F9-button-bar "
    "button:has-text('GO GET IT')"
)
DOWNLOAD_DIALOG = "div.fm-modal-dialog"
DOWNLOAD_BUTTON = "div.fm-download-button"
CLOSE_BUTTON = "div[role='button']:has-text('Close')"

TITLE_SELECTOR = "div.csFM-4BAC3292-04BC-FC44-A278-B61F356EAB45"
METADATA_SELECTORS = {
    "matter_number": "div.fm_object_286",
    "category": "div.fm_object_287",
    "status": "div.fm_object_289",
    "date_received": "div.fm_object_292",
    "decision_date": "div.fm_object_294",
    "outcome": "div.fm_object_295",
    "matter_type": "div.fm_object_298",
}


class UarbScraper(Scraper[DocumentRequest, ScrapeResult]):
    """Drives Playwright to search, navigate, and download documents."""

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config

    def scrape(self, request: DocumentRequest) -> ScrapeResult:
        logger.info(
            "scraping matter=%s doc_types=%s", request.matter_number, request.doc_types
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.config.headless,
                slow_mo=self.config.slow_mo,
                args=["--disable-dev-shm-usage", "--disable-gpu"],
            )
            download_dir = Path(
                tempfile.mkdtemp(prefix=f"uarb_{request.matter_number}_")
            )
            logger.debug(
                "launched browser headless=%s download_dir=%s",
                self.config.headless,
                download_dir,
            )
            try:
                page = browser.new_page()
                page.set_default_timeout(SEARCH_TIMEOUT_MS)
                page.set_default_navigation_timeout(SEARCH_TIMEOUT_MS)
                page.goto(self.config.uarb_base_url)

                self._search_matter(page, request.matter_number, request.doc_types)

                result = ScrapeResult(download_dir=str(download_dir))
                result.metadata = self._extract_metadata(page)
                result.found_counts = {
                    doc_type: self._tab_count(page, doc_type) for doc_type in DOC_TABS
                }
                seen: set[str] = set()
                pending: list[tuple[Download, Path]] = []

                for doc_type in request.doc_types:
                    count = result.found_counts[doc_type]
                    if len(result.documents) >= self.config.max_docs:
                        result.downloaded_counts[doc_type] = 0
                        continue
                    if count <= 0:
                        result.downloaded_counts[doc_type] = 0
                        continue
                    self._click_tab(page, doc_type)
                    self._wait_for_list(page, count)
                    limit = self.config.max_docs - len(result.documents)
                    before = len(result.documents)
                    documents = self._scrape_documents(
                        page, download_dir, limit, doc_type, seen, pending
                    )
                    result.documents.extend(documents)
                    result.downloaded_counts[doc_type] = len(documents)
                    scraped = len(result.documents) - before
                    if scraped != count and scraped != limit:
                        logger.warning(
                            "count mismatch for %s: tab=%d scraped=%d",
                            doc_type,
                            count,
                            scraped,
                        )

                self._save_downloads(pending)
                logger.info(
                    "scrape complete matter=%s downloaded=%d found=%s",
                    request.matter_number,
                    len(result.documents),
                    result.found_counts,
                )
                return result
            except Exception:
                logger.exception("scrape failed matter=%s", request.matter_number)
                shutil.rmtree(download_dir, ignore_errors=True)
                raise
            finally:
                browser.close()
                logger.debug("browser closed for matter=%s", request.matter_number)

    def _extract_metadata(self, page: Page) -> MatterMetadata:
        metadata = MatterMetadata()

        title_loc = page.locator(TITLE_SELECTOR)
        if title_loc.count() > 0:
            metadata.title_description = title_loc.first.inner_text().strip()

        values = {
            name: self._metadata_value(page, selector)
            for name, selector in METADATA_SELECTORS.items()
        }
        metadata.matter_number = values["matter_number"]
        metadata.matter_type = values["matter_type"]
        metadata.status = values["status"]
        metadata.category = values["category"]
        metadata.date_received = values["date_received"]
        metadata.decision_date = values["decision_date"]
        metadata.outcome = values["outcome"]

        return metadata

    @staticmethod
    def _metadata_value(page: Page, selector: str) -> str:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return ""
        text = locator.locator(".text").first
        return text.inner_text().strip() if text.count() > 0 else ""

    def _search_matter(
        self, page: Page, matter_number: str, doc_types: list[str]
    ) -> None:
        """Fill the "Go Directly to Matter" box and submit with Enter."""
        field = self._locate(page, MATTER_FIELD_SELECTORS, "matter field")
        text_div = field.locator("div.text").first
        text_div.click()

        editable = field.locator('div[contenteditable="true"]')
        editable.wait_for(state="visible", timeout=SEARCH_TIMEOUT_MS)
        editable.fill(matter_number)
        editable.press("Enter")

        self._wait_for_results(page, doc_types)

    def _wait_for_results(self, page: Page, doc_types: list[str]) -> None:
        """Wait for the matter detail screen to render after Search."""
        self._locate(page, BACK_TO_RESULTS_SELECTORS, "back to search results")

        for name in doc_types:
            self._tab_locator(page, name).wait_for(
                state="visible", timeout=SEARCH_TIMEOUT_MS
            )

    def _tab_locator(self, page: Page, doc_type: str) -> Locator:
        return page.locator(f"{TAB_BAR} button:has-text('{doc_type}')").first

    def _tab_count(self, page: Page, doc_type: str) -> int:
        """Parse the count from a tab label (e.g. "Key Documents - 3")."""
        label = self._tab_locator(page, doc_type).inner_text().strip()
        if " - " in label:
            try:
                return int(label.rsplit(" - ", 1)[1].strip())
            except ValueError:
                return 0
        return 0

    def _click_tab(self, page: Page, doc_type: str) -> None:
        logger.debug("selecting tab %s", doc_type)
        self._click(self._tab_locator(page, doc_type), label=f"tab {doc_type}")

    @staticmethod
    def _click(locator: Locator, *, label: str) -> None:
        """Click a locator, falling back to a DOM click if an overlay intercepts it.

        FileMaker WebDirect renders header overlays that can sit above grid
        buttons; a normal click then times out with "intercepts pointer events".
        """
        try:
            locator.click(timeout=SEARCH_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.warning("%s click intercepted; dispatching DOM click", label)
            locator.evaluate("el => el.click()")

    def _wait_for_list(self, page: Page, count: int) -> None:
        """Wait for the document list to finish loading for the active tab."""
        page.locator(ROW).first.wait_for(state="visible", timeout=SEARCH_TIMEOUT_MS)
        page.wait_for_function(
            "count => { const t = document.querySelector('table[role=grid]'); "
            "return t && t.getAttribute('aria-rowcount') === String(count); }",
            arg=count,
            timeout=SEARCH_TIMEOUT_MS,
        )

    def _scrape_documents(
        self,
        page: Page,
        download_dir: Path,
        limit: int,
        doc_type: str,
        seen: set[str],
        pending: list[tuple[Download, Path]],
    ) -> list[Document]:
        """Scroll-capture the virtualized list, downloading up to ``limit`` docs.

        Dedupes by Doc No / Exhibit No and returns as soon as no more docs can
        be revealed (scroll produces no new rows) or ``limit`` is reached.
        """
        documents: list[Document] = []
        stable_scrolls = 0
        start = 0

        while len(documents) < limit:
            rows = page.locator(ROW)
            row_count = rows.count()
            identifier = ""
            row = None
            index = start
            while index < row_count:
                candidate = rows.nth(index)
                ident = self._row_identifier(candidate)
                if ident and ident not in seen:
                    identifier = ident
                    row = candidate
                    break
                index += 1

            if row is None:
                moved, at_end = self._scroll_list(page)
                if at_end:
                    break
                stable_scrolls = 0 if moved else stable_scrolls + 1
                if stable_scrolls >= 3:
                    logger.warning(
                        "stopping %s after stable virtual-list scrolls", doc_type
                    )
                    break
                start = 0
                continue

            start = index + 1
            seen.add(identifier)
            title = self._row_title(row)
            file_path = self._download_row(
                page, row, identifier, download_dir, doc_type, pending
            )
            if doc_type == EXHIBITS:
                documents.append(
                    Document(
                        doc_type=doc_type,
                        exhibit_no=identifier,
                        title=title,
                        file_path=file_path,
                    )
                )
            else:
                documents.append(
                    Document(
                        doc_type=doc_type,
                        doc_no=identifier,
                        title=title,
                        file_path=file_path,
                    )
                )
            logger.debug("downloaded %s=%s title=%s", doc_type, identifier, title)

        return documents

    def _row_identifier(self, row: Locator) -> str:
        """Return the row's Doc No / Exhibit No value."""
        for selector in IDENTIFIER_FIELD_SELECTORS:
            locator = row.locator(f"{selector} .text")
            if locator.count() > 0:
                text = locator.first.inner_text().strip()
                if text:
                    return text
        return ""

    def _row_title(self, row: Locator) -> str:
        locator = row.locator(f"{TITLE_FIELD} .text")
        if locator.count() == 0:
            return ""
        return locator.first.inner_text().strip()

    def _download_row(
        self,
        page: Page,
        row: Locator,
        doc_no: str,
        download_dir: Path,
        doc_type: str,
        pending: list[tuple[Download, Path]],
    ) -> str:
        """Trigger a row's download and queue it to be saved later.

        The transfer streams in the background while the browser keeps driving
        the UI, so ``save_as`` is deferred to :meth:`_save_downloads`.
        """
        start = time.perf_counter()
        self._click(row.locator(GO_GET_IT_BUTTON), label="GO GET IT")

        dialog = page.locator(DOWNLOAD_DIALOG).first
        dialog.wait_for(state="visible", timeout=SEARCH_TIMEOUT_MS)

        with page.expect_download(timeout=SEARCH_TIMEOUT_MS) as download_info:
            self._click(dialog.locator(DOWNLOAD_BUTTON).first, label="Download")
        download = download_info.value
        suggested = download.suggested_filename or f"{doc_no}.pdf"
        suffix = Path(suggested).suffix or ".pdf"
        safe_type = get_safe_filename(doc_type, fallback="document")
        safe_doc_no = get_safe_filename(doc_no, fallback="document")
        target = download_dir / f"{safe_type}_{safe_doc_no}{suffix}"
        queued = {path for _, path in pending}
        duplicate = 2
        while target in queued or target.exists():
            target = download_dir / f"{safe_type}_{safe_doc_no}_{duplicate}{suffix}"
            duplicate += 1
        pending.append((download, target))

        if dialog.locator(CLOSE_BUTTON).count() > 0:
            dialog.locator(CLOSE_BUTTON).first.click()
            dialog.wait_for(state="hidden", timeout=SEARCH_TIMEOUT_MS)

        logger.debug(
            "queued download %s=%s in %.3fs",
            doc_type,
            doc_no,
            time.perf_counter() - start,
        )
        return str(target)

    @staticmethod
    def _save_downloads(pending: list[tuple[Download, Path]]) -> None:
        """Persist queued downloads, waiting for each transfer to finish."""
        total_start = time.perf_counter()
        for download, target in pending:
            start = time.perf_counter()
            download.save_as(target)
            logger.debug(
                "saved %s (%d bytes) in %.3fs",
                target.name,
                target.stat().st_size,
                time.perf_counter() - start,
            )
        logger.debug(
            "saved %d download(s) in %.3fs",
            len(pending),
            time.perf_counter() - total_start,
        )

    def _scroll_list(self, page: Page) -> tuple[bool, bool]:
        """Scroll the virtualized grid down, overlapping the current window.

        Returns True if new rows were revealed, False if already at the end.
        """
        scroller = page.locator(".v-grid-scroller-vertical").first

        metrics = scroller.evaluate(
            "el => ({ scrollTop: el.scrollTop, clientHeight: el.clientHeight, "
            "scrollHeight: el.scrollHeight })"
        )
        scroll_bottom = metrics["scrollTop"] + metrics["clientHeight"]
        if scroll_bottom >= metrics["scrollHeight"] - 1:
            return False, True

        row = page.locator(ROW).first
        try:
            row_height = row.evaluate("el => el.getBoundingClientRect().height")
        except Exception:  # noqa: BLE001 - fall back to a sane default
            row_height = 68

        body = page.locator("tbody.v-grid-body").first
        before = body.evaluate("el => el.style.transform")

        step = max(metrics["clientHeight"] - row_height, row_height)
        scroller.evaluate(f"el => {{ el.scrollTop += {step}; }}")

        try:
            page.wait_for_function(
                "before => document.querySelector('tbody.v-grid-body')"
                "?.style.transform !== before",
                arg=before,
                timeout=SCROLL_TIMEOUT_MS,
            )
            return True, False
        except Exception:  # noqa: BLE001 - no more rows to reveal
            after = scroller.evaluate("el => el.scrollTop")
            return after != metrics["scrollTop"], False

    def _locate(self, page: Page, selectors: tuple[str, ...], label: str) -> Locator:
        """Return the first selector that becomes visible, logging the tier fired.

        FileMaker WebDirect renders its widgets asynchronously after the page's
        ``load`` event, so each tier is awaited (not just probed immediately).
        """
        for selector in selectors:
            locator = page.locator(selector)
            try:
                locator.first.wait_for(state="visible", timeout=SEARCH_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001 - try the next tier
                logger.debug("tier %s for %s not found: %s", selector, label, exc)
                continue
            logger.debug("matched %s with tier: %s", label, selector)
            return locator.first
        raise RuntimeError(f"no selector matched for {label}")

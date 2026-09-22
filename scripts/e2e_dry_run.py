"""Run the scrape -> ZIP pipeline without IMAP, SMTP, or LLM calls."""

import argparse
import shutil
import zipfile
from pathlib import Path

from regulatory_agent.browser.uarb_scraper import UarbScraper
from regulatory_agent.config import load_config
from regulatory_agent.main import handle_request
from regulatory_agent.models.uarb_request import DocumentRequest, ScrapeResult


class DryRunSender:
    """Capture the generated ZIP locally instead of sending email."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def send_reply(
        self,
        request: DocumentRequest,
        *,
        result: ScrapeResult | None = None,
        attachment_path: str = "",
        error: str | None = None,
    ) -> None:
        if error is not None:
            raise RuntimeError(f"dry run request failed: {error}")
        if result is None:
            raise ValueError("dry run summary reply needs a result")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.output_dir / Path(attachment_path).name
        shutil.copy2(attachment_path, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
        print(f"metadata: {result.metadata}")
        print(f"found: {result.found_counts}")
        print(f"downloaded: {result.downloaded_counts}")
        print(f"archive: {archive_path}")
        print(f"files: {len(names)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matter_number")
    parser.add_argument("doc_types", nargs="+", help="document tab names")
    parser.add_argument("--output-dir", type=Path, default=Path("e2e-output"))
    args = parser.parse_args()

    request = DocumentRequest(
        matter_number=args.matter_number,
        doc_types=args.doc_types,
        from_address="dry-run@example.invalid",
        valid=True,
    )
    config = load_config()
    handle_request(
        request,
        scraper=UarbScraper(config.browser),
        sender=DryRunSender(args.output_dir),
    )


if __name__ == "__main__":
    main()

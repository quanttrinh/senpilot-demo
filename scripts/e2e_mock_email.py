"""Run the email-to-ZIP workflow from a local .eml file only."""

import argparse
from pathlib import Path

from regulatory_agent.browser.uarb_scraper import UarbScraper
from regulatory_agent.config import EmailConfig, load_config
from regulatory_agent.email.sender import EmailSmtpSender
from regulatory_agent.email.uarb_request_reader import UarbRequestReader
from regulatory_agent.email.uarb_request_responder import RequestResponder
from regulatory_agent.llm import OpenCodeLlmClient
from regulatory_agent.main import handle_request


class LocalEmailSender(EmailSmtpSender):
    """Generate the real reply but capture it instead of using SMTP."""

    def __init__(self, config: EmailConfig, *, output_dir: Path) -> None:
        super().__init__(config)
        self.output_dir = output_dir

    def _send(self, message) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / "captured-reply.eml"
        output_path.write_bytes(message.as_bytes())
        print(f"captured email: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email", type=Path, help="path to a local .eml file")
    parser.add_argument("--output-dir", type=Path, default=Path("e2e-output"))
    args = parser.parse_args()

    config = load_config()
    llm = OpenCodeLlmClient(config.llm.base_url, config.llm.model, config.llm.api_key)
    request = UarbRequestReader(llm).to_request(args.email.read_bytes())
    print(f"request: {request}")

    transport = LocalEmailSender(config.email, output_dir=args.output_dir)
    handle_request(
        request,
        scraper=UarbScraper(config.browser),
        sender=RequestResponder(transport, llm),
    )


if __name__ == "__main__":
    main()

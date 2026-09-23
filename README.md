# Senpilot Regulatory Agent Demo

An agent that reads emailed requests for UARB documents and replies with the files, unattended, 24/7.

Email in → LLM parse → headless-browser scrape → ZIP → reply. Runs as a resilient systemd service.

## Demo

[Watch the demo video](senpilot_demo.webm) · also embedded in [`presentation.html`](presentation.html).

[senpilot_demo.webm](https://github.com/user-attachments/assets/72c90980-6b69-4d1a-8bde-a12d2acc9b10)

## Presentation

Open [`presentation.html`](presentation.html) in a browser from this directory.

## Layout

- `regulatory_agent/email/` — IMAP listener, request reader, reply responder, SMTP sender
- `regulatory_agent/browser/` — templated `Scraper` ABC and the UARB scraper
- `regulatory_agent/llm/` — `LlmClient` ABC and the OpenCode provider
- `regulatory_agent/utils/` — archive, systemd, prompt-guard, and template helpers
- `scripts/` — Ubuntu setup and end-to-end test scripts

## Checks

```sh
uv run ruff check .
uv run mypy
uv run ruff format --check .
```

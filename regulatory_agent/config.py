"""Application configuration.

Settings are sourced from environment variables, with defaults matching the
assignment's target deployment (Zoho Mail + UARB WebDirect).
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(slots=True)
class EmailConfig:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    inbox_folder: str
    idle_timeout_min: int


@dataclass(slots=True)
class BrowserConfig:
    uarb_base_url: str
    max_docs: int
    headless: bool
    slow_mo: int


@dataclass(slots=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str


@dataclass(slots=True)
class AgentConfig:
    email: EmailConfig
    browser: BrowserConfig
    llm: LlmConfig


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int_env(
    name: str, default: str, *, minimum: int = 1, maximum: int | None = None
) -> int:
    raw = _env(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


def load_config() -> AgentConfig:
    """Load configuration from environment variables."""
    load_dotenv()
    email = EmailConfig(
        imap_host=_env("UARB_IMAP_HOST", "imap.zoho.com"),
        imap_port=_int_env("UARB_IMAP_PORT", "993", maximum=65535),
        smtp_host=_env("UARB_SMTP_HOST", "smtp.zoho.com"),
        smtp_port=_int_env("UARB_SMTP_PORT", "587", maximum=65535),
        username=_env("UARB_EMAIL_USER"),
        password=_env("UARB_EMAIL_PASS"),
        inbox_folder=_env("UARB_IMAP_FOLDER", "INBOX"),
        idle_timeout_min=_int_env("UARB_IDLE_TIMEOUT_MIN", "25"),
    )
    browser = BrowserConfig(
        uarb_base_url=_env(
            "UARB_BASE_URL", "https://uarb.novascotia.ca/fmi/webd/UARB15"
        ),
        max_docs=_int_env("UARB_MAX_DOCS", "10"),
        headless=_env("UARB_HEADLESS", "1") not in ("0", "false", "False"),
        slow_mo=_int_env("UARB_SLOW_MO", "0", minimum=0),
    )
    llm = LlmConfig(
        base_url=_env("UARB_LLM_BASE_URL", "https://opencode.ai/zen/go/v1"),
        model=_env("UARB_LLM_MODEL", "deepseek-v4.1-flash"),
        api_key=_env("UARB_LLM_API_KEY"),
    )
    return AgentConfig(email=email, browser=browser, llm=llm)


def require_credentials(config: AgentConfig) -> None:
    """Raise a clear error when settings needed to run the agent are missing."""
    required = {
        "UARB_EMAIL_USER": config.email.username,
        "UARB_EMAIL_PASS": config.email.password,
        "UARB_LLM_API_KEY": config.llm.api_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError("missing required settings: " + ", ".join(missing))

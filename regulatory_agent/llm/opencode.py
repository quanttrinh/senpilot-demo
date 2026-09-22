"""LLM client variant for the OpenCode Zen gateway.

The base :class:`LlmClient` stays provider-agnostic; this subclass tags each
request with the OpenCode session/agent metadata the gateway understands.
"""

import logging

from regulatory_agent.llm.client import LlmClient

logger = logging.getLogger(__name__)

DEFAULT_AGENT = "senpilot-regulatory-agent"


class OpenCodeLlmClient(LlmClient):
    """OpenAI-compatible client that sends OpenCode-specific request headers."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        agent: str = DEFAULT_AGENT,
        client_name: str = "senpilot-regulatory-agent",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(base_url, model, api_key)
        self.agent = agent
        self.client_name = client_name
        self.extra_headers = dict(extra_headers or {})

    def request_headers(self, session_id: str) -> dict[str, str]:
        """Return OpenCode headers, keyed by the active session."""
        headers = {
            "x-opencode-session": session_id,
            "x-opencode-client": self.client_name,
        }
        if self.agent:
            headers["x-opencode-agent"] = self.agent
        headers.update(self.extra_headers)
        logger.debug("opencode headers session=%s agent=%s", session_id, self.agent)
        return headers

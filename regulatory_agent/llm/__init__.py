"""LLM integration via an OpenAI-compatible endpoint."""

from regulatory_agent.llm.client import LlmClient
from regulatory_agent.llm.opencode import OpenCodeLlmClient

__all__ = ["LlmClient", "OpenCodeLlmClient"]

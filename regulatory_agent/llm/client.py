"""LLM client backed by the OpenAI SDK against an OpenAI-compatible endpoint."""

import logging
import uuid
from abc import ABC, abstractmethod
from collections import deque

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 30
LLM_MAX_RETRIES = 2


class LlmClient(ABC):
    """OpenAI-compatible chat client with in-memory session (conversation) state."""

    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers={"User-Agent": "senpilot-regulatory-agent-demo/1.0"},
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
        )
        self.model = model
        self._sessions: dict[str, deque[ChatCompletionMessageParam]] = {}
        self._session_order: deque[str] = deque()
        self.max_sessions = 100
        self.max_messages = 20
        logger.debug("llm client ready model=%s base_url=%s", model, base_url)

    def create_session(self) -> str:
        """Create a new session and return its id."""
        session_id = uuid.uuid4().hex
        self._ensure_session(session_id)
        logger.debug("created llm session %s", session_id)
        return session_id

    def _ensure_session(self, session_id: str) -> None:
        """Register ``session_id`` if new, evicting the oldest sessions."""
        if session_id in self._sessions:
            return
        self._sessions[session_id] = deque(maxlen=self.max_messages)
        self._session_order.append(session_id)
        while len(self._session_order) > self.max_sessions:
            expired = self._session_order.popleft()
            self._sessions.pop(expired, None)
            logger.debug("evicted old llm session %s", expired)

    def delete_session(self, session_id: str) -> None:
        """Release local conversation history after a request is complete."""
        self._sessions.pop(session_id, None)
        try:
            self._session_order.remove(session_id)
        except ValueError:
            pass
        logger.debug("deleted llm session %s", session_id)

    @abstractmethod
    def request_headers(self, session_id: str) -> dict[str, str]:
        """Provider-specific headers to tag a completion request."""

    def send_message(
        self, session_id: str, text: str, *, model: str | None = None
    ) -> str:
        """Send a message in a session and return the assistant's reply text."""
        self._ensure_session(session_id)
        history = self._sessions[session_id]
        history.append({"role": "user", "content": text})

        active_model = model or self.model
        logger.debug(
            "llm request session=%s model=%s messages=%d",
            session_id,
            active_model,
            len(history),
        )
        try:
            response = self.client.chat.completions.create(
                model=active_model,
                messages=list(history),
                extra_headers=self.request_headers(session_id),
            )
        except Exception:
            logger.exception("llm request failed session=%s", session_id)
            raise

        reply = response.choices[0].message.content or ""
        history.append({"role": "assistant", "content": reply})
        logger.debug("llm reply session=%s chars=%d", session_id, len(reply))
        return reply

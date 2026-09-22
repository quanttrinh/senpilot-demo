"""Turns raw inbound email messages into parsed DocumentRequests."""

import json
import logging
import re
from email import message_from_bytes
from email.message import Message
from email.utils import parseaddr

from regulatory_agent.constants import DOC_TYPES
from regulatory_agent.llm import LlmClient
from regulatory_agent.models.uarb_request import DocumentRequest
from regulatory_agent.utils.prompt import INJECTION_GUARD, wrap_untrusted
from regulatory_agent.utils.template import render

logger = logging.getLogger(__name__)

_MATTER_NUMBER = re.compile(r"M\d{5}", re.IGNORECASE)
_NAME_ALLOWED = re.compile(r"[^\w .'\-]", re.UNICODE)


class UarbRequestReader:
    """Reads a raw RFC 822 message into a parsed :class:`DocumentRequest`.

    Owns both the MIME-level extraction (sender, subject, body) and the LLM
    parsing (matter number and document types).
    """

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    def to_request(self, raw: bytes) -> DocumentRequest:
        """Parse raw message bytes into a DocumentRequest."""
        msg = message_from_bytes(raw)
        from_name, from_address = parseaddr(msg.get("From", ""))
        reply_to = parseaddr(msg.get("Reply-To", "") or msg.get("From", ""))[1]
        logger.debug(
            "parsing inbound email from=%s reply_to=%s subject=%r",
            from_address,
            reply_to,
            msg.get("Subject", ""),
        )

        request = self._parse_request(
            self._body(msg),
            reply_to=reply_to,
            from_address=from_address,
        )
        if not request.requester_name:
            request.requester_name = UarbRequestReader._clean_name(from_name)
        request.subject = msg.get("Subject", "")
        request.message_id = msg.get("Message-ID", "")
        return request

    def _parse_request(
        self, raw_body: str, reply_to: str, from_address: str
    ) -> DocumentRequest:
        """Extract the request fields from the body via the LLM."""
        request = DocumentRequest(
            reply_to=reply_to,
            from_address=from_address,
        )

        llm_result, session_id = self._llm_parse(raw_body)
        request.session_id = session_id or ""
        llm_result = llm_result or {}
        request.matter_number = str(llm_result.get("matter_number", "")).upper()
        request.doc_types = list(llm_result.get("doc_types", []))
        request.requester_name = str(llm_result.get("requester_name", ""))

        if not self._is_valid_matter_number(request.matter_number):
            request.error = "missing or invalid matter number"
        elif not request.doc_types or any(
            d not in DOC_TYPES for d in request.doc_types
        ):
            request.error = "missing or unrecognized document type"
        else:
            request.valid = True

        if request.valid:
            logger.info(
                "parsed request matter=%s doc_types=%s",
                request.matter_number,
                request.doc_types,
            )
        else:
            logger.warning(
                "could not parse request from=%s: %s (matter=%r doc_types=%s)",
                request.from_address,
                request.error,
                request.matter_number,
                request.doc_types,
            )
        return request

    @staticmethod
    def _is_valid_matter_number(matter_number: str) -> bool:
        """Validate a UARB matter number (e.g. M01234)."""
        return (
            bool(matter_number) and _MATTER_NUMBER.fullmatch(matter_number) is not None
        )

    def _llm_parse(self, body: str) -> tuple[dict | None, str | None]:
        """Extract matter number + doc types from an email via the LLM.

        Returns the parsed ``(result, session_id)`` so callers can reuse the
        session to carry the original email's context into later steps.
        """
        session_id = self.client.create_session()
        try:
            reply = self.client.send_message(session_id, self._parse_prompt(body))
        except Exception:  # noqa: BLE001 - parsing must never raise
            logger.exception("llm parse failed; treating email as unparseable")
            self.client.delete_session(session_id)
            return None, None

        result = self._parse_llm_json(reply)
        if not result:
            logger.warning("llm returned unparseable JSON: %r", reply[:200])
            return None, session_id

        raw_doc_types = result.get("doc_types", [])
        if isinstance(raw_doc_types, str):
            raw_doc_types = [raw_doc_types]
        if not isinstance(raw_doc_types, list):
            raw_doc_types = []

        canonical = [self._canonicalize_doc_type(str(d)) for d in raw_doc_types]
        return {
            "matter_number": str(result.get("matter_number", "")),
            "doc_types": list(dict.fromkeys(canonical)),
            "requester_name": UarbRequestReader._clean_name(
                str(result.get("requester_name", ""))
            ),
        }, session_id

    @staticmethod
    def _parse_prompt(body: str) -> str:
        """Build the extraction prompt for one email body."""
        doc_types = ", ".join(doc_type.value for doc_type in DOC_TYPES)
        return render(
            t"Extract the UARB matter number, the requested document types, and "
            t"the sender's name from the email below. Respond with a single JSON "
            t"object and nothing else, of the form {{"
            t'"matter_number": "M01234", "doc_types": ["Exhibits", "Transcripts"], '
            t'"requester_name": "Jane Doe"'
            t"}}.\n"
            t"Valid document types: {doc_types}.\n"
            t"The email may request more than one document type; list all of them.\n"
            t"Use the sender's own name (from the sign-off), or an empty string if "
            t"it is not stated.\n"
            t"Use an empty array if none are requested (e.g. {{"
            t'"matter_number": "", "doc_types": [], "requester_name": ""'
            t"}}).\n\n"
            t"{INJECTION_GUARD}\n\n"
            t"Email:\n"
            t"{wrap_untrusted(body)}"
        )

    @staticmethod
    def _canonicalize_doc_type(doc_type: str) -> str:
        """Return the canonical doc type for a (possibly case-different) input."""
        stripped = doc_type.strip()
        lowered = stripped.lower()
        for canonical in DOC_TYPES:
            if canonical.value.lower() == lowered:
                return canonical.value
        return stripped

    @staticmethod
    def _parse_llm_json(text: str) -> dict | None:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _body(msg: Message) -> str:
        """Return the plain-text body, falling back to HTML."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return UarbRequestReader._decode(part)
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    return UarbRequestReader._decode(part)
            return ""
        return UarbRequestReader._decode(msg)

    @staticmethod
    def _decode(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")

    @staticmethod
    def _clean_name(name: str, *, max_length: int = 80) -> str:
        """Return a tidy single-line display name, or "" if nothing usable."""
        cleaned = " ".join(_NAME_ALLOWED.sub(" ", name).split())
        return cleaned[:max_length].strip()

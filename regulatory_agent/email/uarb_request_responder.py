"""UARB-specific reply composition and orchestration."""

import json
import logging

from regulatory_agent.email.sender import EmailSmtpSender
from regulatory_agent.llm import LlmClient
from regulatory_agent.models.uarb_request import DocumentRequest, ScrapeResult
from regulatory_agent.utils.prompt import INJECTION_GUARD, wrap_untrusted
from regulatory_agent.utils.template import render

logger = logging.getLogger(__name__)


class RequestResponder:
    """Turns a processed request into a summary or error reply.

    Owns the domain logic (LLM summary, UARB wording, recipients, subjects)
    and delegates delivery to a generic :class:`EmailSmtpSender`.
    """

    def __init__(self, sender: EmailSmtpSender, llm: LlmClient) -> None:
        self.sender = sender
        self.llm = llm

    def send_reply(
        self,
        request: DocumentRequest,
        *,
        result: ScrapeResult | None = None,
        attachment_path: str = "",
        error: str | None = None,
    ) -> None:
        """Send the summary reply when a result is given, else the error reply."""
        logger.debug(
            "composing reply matter=%s has_result=%s has_error=%s",
            request.matter_number,
            result is not None,
            error is not None,
        )
        if error is not None:
            self._send_error(request, error)
        elif result is not None:
            self._send_summary(request, result, attachment_path)
        else:
            raise ValueError("send_reply needs a result or an error")

    def _send_summary(
        self, request: DocumentRequest, result: ScrapeResult, attachment_path: str
    ) -> None:
        """Generate the summary body via the LLM and reply with the ZIP attached."""
        recipient = self._recipient(request)
        logger.info(
            "sending summary reply to=%s matter=%s attachment=%s",
            recipient,
            request.matter_number,
            attachment_path or "none",
        )
        try:
            body = self._greeting(request) + self._generate_summary(request, result)
            logger.debug("generated summary body (%d chars)", len(body))
            self.sender.send(
                to=recipient,
                subject=self._summary_subject(request),
                body=body,
                attachment_path=attachment_path,
                in_reply_to=request.message_id,
            )
        finally:
            self.llm.delete_session(request.session_id)

    def _send_error(self, request: DocumentRequest, error: str) -> None:
        """Tell the requester why the document request could not be processed."""
        recipient = self._recipient(request)
        logger.info("sending error reply to=%s: %s", recipient, error)
        try:
            self.sender.send(
                to=recipient,
                subject=self._reply_subject(request.subject),
                body=(
                    f"{self._greeting(request)}"
                    f"I could not process your UARB document request: {error}."
                ),
                in_reply_to=request.message_id,
            )
        finally:
            self.llm.delete_session(request.session_id)

    @staticmethod
    def _greeting(request: DocumentRequest) -> str:
        if not request.requester_name:
            return ""
        return f"Hi {request.requester_name},\n\n"

    @staticmethod
    def _recipient(request: DocumentRequest) -> str:
        to = request.reply_to or request.from_address
        if not to:
            raise ValueError("request has no reply recipient")
        return to

    def _summary_subject(self, request: DocumentRequest) -> str:
        if request.message_id:
            return self._reply_subject(request.subject)
        return f"UARB documents for {request.matter_number}"

    @staticmethod
    def _reply_subject(subject: str) -> str:
        stripped = subject.strip()
        if not stripped:
            return "UARB documents"
        return stripped if stripped.lower().startswith("re:") else f"Re: {stripped}"

    def _generate_summary(self, request: DocumentRequest, result: ScrapeResult) -> str:
        data = self._summarize(result, matter_number=request.matter_number)
        if not request.session_id:
            request.session_id = self.llm.create_session()
        return self.llm.send_message(
            request.session_id, self._summary_prompt(json.dumps(data, indent=2))
        )

    @staticmethod
    def _summary_prompt(data: str) -> str:
        """Build the reply-summary prompt for one scrape result."""
        return render(
            t"Write the body of a reply email summarizing the UARB document "
            t"retrieval whose data is provided below, in prose like this example:\n"
            t'"M12205 is about the <title>. It relates to <type> within the '
            t"<category> category. The matter had an initial filing on <date> and "
            t"a final filing on <date>. I found <counts across all types>. I "
            t"downloaded <n> out of <m> <type(s)> and am attaching them as a ZIP "
            t'here."\n'
            t"Use the actual values below. List the received and decision dates "
            t"separately. List found counts for every document type, and state how "
            t"many were downloaded. Convert dates to a natural form "
            t"(e.g. '04/07/2025' -> 'April 7, 2025').\n"
            t"Respond with the email body only: no subject line, no "
            t"greeting/signature, and no markdown code fences.\n\n"
            t"{INJECTION_GUARD}\n\n"
            t"Data:\n"
            t"{wrap_untrusted(data)}"
        )

    @staticmethod
    def _summarize(result: ScrapeResult, *, matter_number: str) -> dict:
        metadata = result.metadata
        return {
            "matter_number": matter_number,
            "metadata": {
                "matter_number": metadata.matter_number,
                "title_description": metadata.title_description,
                "category": metadata.category,
                "matter_type": metadata.matter_type,
                "date_received": metadata.date_received,
                "decision_date": metadata.decision_date,
                "outcome": metadata.outcome,
                "status": metadata.status,
            },
            "downloaded_count": len(result.documents),
            "found_counts": result.found_counts,
            "downloaded_counts": result.downloaded_counts,
            "documents": [
                {
                    "doc_type": d.doc_type,
                    "exhibit_no": d.exhibit_no,
                    "doc_no": d.doc_no,
                    "title": d.title,
                }
                for d in result.documents
            ],
        }

"""Domain models for requests, documents, and metadata."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class DocumentRequest:
    """A parsed inbound request for UARB documents."""

    matter_number: str = ""
    doc_types: list[str] = field(default_factory=list)
    reply_to: str = ""
    from_address: str = ""
    requester_name: str = ""
    subject: str = ""
    message_id: str = ""
    session_id: str = ""
    valid: bool = False
    error: str | None = None


@dataclass(slots=True)
class Document:
    """A single scraped document."""

    doc_type: str = ""
    exhibit_no: str = ""
    doc_no: str = ""
    title: str = ""
    file_path: str = ""


@dataclass(slots=True)
class MatterMetadata:
    """Matter detail fields displayed by the UARB interface."""

    matter_number: str = ""
    status: str = ""
    title_description: str = ""
    matter_type: str = ""
    category: str = ""
    date_received: str = ""
    decision_date: str = ""
    outcome: str = ""


@dataclass(slots=True)
class ScrapeResult:
    """Outcome of a browser scrape for one matter."""

    documents: list[Document] = field(default_factory=list)
    metadata: MatterMetadata = field(default_factory=MatterMetadata)
    found_counts: dict[str, int] = field(default_factory=dict)
    downloaded_counts: dict[str, int] = field(default_factory=dict)
    download_dir: str = ""

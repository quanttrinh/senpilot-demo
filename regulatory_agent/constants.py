"""Project-wide constants."""

from enum import StrEnum


class DocumentType(StrEnum):
    """Document tabs exposed by the UARB interface."""

    EXHIBITS = "Exhibits"
    KEY_DOCUMENTS = "Key Documents"
    OTHER_DOCUMENTS = "Other Documents"
    TRANSCRIPTS = "Transcripts"
    RECORDINGS = "Recordings"


DOC_TYPES = frozenset(DocumentType)

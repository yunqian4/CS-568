"""Representation helpers for extracted PDF blocks."""

from ..models import PdfBlockRepresentation
from .keywords import build_placeholder_keywords
from .llm import LlmRepresentationConfig, enrich_document_representations
from .summaries import build_placeholder_summary


def build_default_block_representations(text: str) -> list[PdfBlockRepresentation]:
    """Build the default block representations shown by the reader."""

    return [
        PdfBlockRepresentation(
            kind="keywords",
            label="Keywords",
            items=[keyword.label for keyword in build_placeholder_keywords(text)],
        ),
        PdfBlockRepresentation(
            kind="summary",
            label="Summary",
            text=build_placeholder_summary(text),
        ),
    ]


__all__ = [
    "build_default_block_representations",
    "build_placeholder_keywords",
    "build_placeholder_summary",
    "enrich_document_representations",
    "LlmRepresentationConfig",
]

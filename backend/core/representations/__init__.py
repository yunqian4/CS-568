"""Representation helpers for extracted PDF blocks."""

from ..models import PdfBlockRepresentation
from .keywords import build_placeholder_keywords
from .llm import LlmRepresentationConfig, RepresentationDefinition, enrich_document_representations
from .summaries import build_placeholder_summary


def build_default_block_representations(text: str) -> list[PdfBlockRepresentation]:
    """Build the default block representations shown by the reader."""

    keywords = [keyword.label for keyword in build_placeholder_keywords(text)]
    summary = build_placeholder_summary(text)
    return [
        PdfBlockRepresentation(
            kind="keywords",
            label="Keywords",
            value=", ".join(keywords),
            background_color="#7a4a12",
            background_opacity=1.0,
            items=keywords,
        ),
        PdfBlockRepresentation(
            kind="summary",
            label="Summary",
            value=summary,
            background_color="#263238",
            background_opacity=1.0,
            text=summary,
        ),
    ]


__all__ = [
    "build_default_block_representations",
    "build_placeholder_keywords",
    "build_placeholder_summary",
    "enrich_document_representations",
    "LlmRepresentationConfig",
    "RepresentationDefinition",
]

"""Provider contracts for raw PDF parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedPdfChunk:
    """Raw text chunk geometry returned by a parser provider."""

    chunk_id: str
    page_number: int
    text: str
    x: float
    y: float
    width: float
    height: float
    font_size: float | None = None
    font_name: str | None = None
    is_bold: bool | None = None
    semantic_type: str | None = None
    heading_level: int | None = None


@dataclass(slots=True)
class ParsedPdfPage:
    """Raw page output returned by a parser provider."""

    page_number: int
    width: float
    height: float
    chunks: list[ParsedPdfChunk] = field(default_factory=list)


@dataclass(slots=True)
class ParsedPdfDocument:
    """Provider output before zoomable-document construction."""

    title: str
    pages: list[ParsedPdfPage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

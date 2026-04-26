"""Document models shared across the PDF reader API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PdfChunkKeyword:
    """Placeholder keyword anchored to a text chunk."""

    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label}


@dataclass(slots=True)
class PdfBlockRepresentation:
    """A derived representation attached to a leaf block."""

    kind: str
    label: str
    text: str | None = None
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "text": self.text,
            "items": list(self.items),
        }


@dataclass(slots=True)
class PdfChunk:
    """A text chunk extracted from a page with normalized bounds."""

    chunk_id: str
    page_number: int
    text: str
    x: float
    y: float
    width: float
    height: float
    font_size: float | None = None
    keywords: list[PdfChunkKeyword] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "page_number": self.page_number,
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
            "keywords": [keyword.to_dict() for keyword in self.keywords],
            "block_ids": list(self.block_ids),
        }


@dataclass(slots=True)
class PdfBlock:
    """A paragraph leaf block in the zoomable document tree."""

    block_id: str
    page_number: int
    text: str
    chunk_ids: list[str] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    representations: list[PdfBlockRepresentation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "page_number": self.page_number,
            "text": self.text,
            "chunk_ids": list(self.chunk_ids),
            "section_path": list(self.section_path),
            "representations": [representation.to_dict() for representation in self.representations],
        }


@dataclass(slots=True)
class ZoomableNode:
    """A node in the zoomable document tree."""

    node_id: str
    title: str
    node_type: str
    level: int | None = None
    page_number: int | None = None
    block_id: str | None = None
    text: str | None = None
    chunk_ids: list[str] = field(default_factory=list)
    children: list["ZoomableNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "node_type": self.node_type,
            "level": self.level,
            "page_number": self.page_number,
            "block_id": self.block_id,
            "text": self.text,
            "chunk_ids": list(self.chunk_ids),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class PdfPage:
    """A single PDF page and its extracted chunks."""

    page_number: int
    width: float
    height: float
    chunks: list[PdfChunk] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }


@dataclass(slots=True)
class PdfDocument:
    """Serialized document returned to the frontend reader."""

    document_id: str
    title: str
    source_name: str
    page_count: int
    pages: list[PdfPage] = field(default_factory=list)
    blocks: list[PdfBlock] = field(default_factory=list)
    zoomable_document: ZoomableNode | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "source_name": self.source_name,
            "page_count": self.page_count,
            "pages": [page.to_dict() for page in self.pages],
            "blocks": [block.to_dict() for block in self.blocks],
            "zoomable_document": self.zoomable_document.to_dict() if self.zoomable_document else None,
            "metadata": dict(self.metadata),
        }

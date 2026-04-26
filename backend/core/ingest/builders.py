"""Build serialized PDF documents from provider output."""

from __future__ import annotations

from ..models import PdfChunk, PdfDocument, PdfPage
from ..representations import build_placeholder_keywords
from .contracts import ParsedPdfDocument
from .semantic import build_semantic_document_plan


def build_pdf_document_from_parsed_pdf(
    document_id: str,
    source_name: str,
    provider: str,
    parsed_document: ParsedPdfDocument,
) -> PdfDocument:
    """Convert provider output into the reader document contract."""

    semantic_plan = build_semantic_document_plan(parsed_document=parsed_document, source_name=source_name)

    pages: list[PdfPage] = []
    for parsed_page in parsed_document.pages:
        page_chunks = [
            PdfChunk(
                chunk_id=parsed_chunk.chunk_id,
                page_number=parsed_chunk.page_number,
                text=parsed_chunk.text,
                x=parsed_chunk.x,
                y=parsed_chunk.y,
                width=parsed_chunk.width,
                height=parsed_chunk.height,
                font_size=parsed_chunk.font_size,
                keywords=build_placeholder_keywords(parsed_chunk.text),
                block_ids=semantic_plan.chunk_to_block_ids.get(parsed_chunk.chunk_id, []),
            )
            for parsed_chunk in parsed_page.chunks
        ]
        pages.append(
            PdfPage(
                page_number=parsed_page.page_number,
                width=parsed_page.width,
                height=parsed_page.height,
                chunks=page_chunks,
            )
        )

    metadata = dict(parsed_document.metadata)
    metadata["provider"] = provider
    metadata.update(semantic_plan.metadata)

    return PdfDocument(
        document_id=document_id,
        title=semantic_plan.title,
        source_name=source_name,
        page_count=len(pages),
        pages=pages,
        blocks=semantic_plan.blocks,
        zoomable_document=semantic_plan.zoomable_document,
        metadata=metadata,
    )

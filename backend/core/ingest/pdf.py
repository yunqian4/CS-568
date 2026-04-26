"""Native PDF parsing based on PyMuPDF text blocks."""

from __future__ import annotations

import re
from statistics import median

import fitz

from .contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage

SPACE_RE = re.compile(r"\s+")


def parse_native_pdf_bytes(source_name: str, pdf_bytes: bytes) -> ParsedPdfDocument:
    """Extract raw text blocks with geometry from a PDF."""

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages: list[ParsedPdfPage] = []

        for page_index, page in enumerate(document, start=1):
            page_width = float(page.rect.width or 1.0)
            page_height = float(page.rect.height or 1.0)
            blocks = _extract_text_blocks(
                page_index=page_index,
                page_width=page_width,
                page_height=page_height,
                block_dicts=page.get_text("dict").get("blocks", []),
            )
            pages.append(
                ParsedPdfPage(
                    page_number=page_index,
                    width=page_width,
                    height=page_height,
                    chunks=blocks,
                )
            )

        title = (document.metadata or {}).get("title") or source_name
        return ParsedPdfDocument(
            title=str(title).strip() or source_name,
            pages=pages,
            metadata={"parser": "pymupdf"},
        )
    finally:
        document.close()


def _extract_text_blocks(
    page_index: int,
    page_width: float,
    page_height: float,
    block_dicts: list[dict[str, object]],
) -> list[ParsedPdfChunk]:
    """Convert PyMuPDF text blocks into raw parsed chunks."""

    chunks: list[ParsedPdfChunk] = []
    chunk_index = 1

    for block in block_dicts:
        if int(block.get("type", -1)) != 0:
            continue

        text = _normalize_block_text(block)
        if not text:
            continue

        x0, y0, x1, y1 = [float(value) for value in block.get("bbox", (0.0, 0.0, 0.0, 0.0))]
        width = max(x1 - x0, 0.0)
        height = max(y1 - y0, 0.0)
        if width <= 0.0 or height <= 0.0:
            continue

        chunks.append(
            ParsedPdfChunk(
                chunk_id=f"chunk-{page_index:03d}-{chunk_index:04d}",
                page_number=page_index,
                text=text,
                x=_clamp(x0 / page_width),
                y=_clamp(y0 / page_height),
                width=_clamp(width / page_width),
                height=_clamp(height / page_height),
                font_size=_extract_block_font_size(block),
                font_name=_extract_block_font_name(block),
                is_bold=_extract_block_is_bold(block),
            )
        )
        chunk_index += 1

    return chunks


def _normalize_block_text(block: dict[str, object]) -> str:
    """Flatten block lines into one readable paragraph string."""

    parts: list[str] = []
    for line in block.get("lines", []):
        line_text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
        normalized_line = SPACE_RE.sub(" ", line_text).strip()
        if not normalized_line:
            continue
        _append_line(parts, normalized_line)

    return " ".join(part.strip() for part in parts if part.strip())


def _append_line(parts: list[str], line_text: str) -> None:
    """Append one line while repairing simple hyphenated wraps."""

    if not parts:
        parts.append(line_text)
        return

    previous = parts[-1]
    if previous.endswith("-") and line_text[:1].islower():
        parts[-1] = previous[:-1] + line_text
        return

    parts.append(line_text)


def _extract_block_font_size(block: dict[str, object]) -> float | None:
    """Estimate one representative font size for a text block."""

    sizes = [
        float(span["size"])
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("size") is not None
    ]
    return median(sizes) if sizes else None


def _extract_block_font_name(block: dict[str, object]) -> str | None:
    """Return one representative font name for a text block."""

    for line in block.get("lines", []):
        for span in line.get("spans", []):
            font_name = str(span.get("font", "")).strip()
            if font_name:
                return font_name
    return None


def _extract_block_is_bold(block: dict[str, object]) -> bool | None:
    """Return whether any span in the block is marked bold by PyMuPDF."""

    found_span = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            found_span = True
            if int(span.get("flags", 0)) & fitz.TEXT_FONT_BOLD:
                return True
    return False if found_span else None


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)

"""GROBID-backed paper parsing with PyMuPDF geometry alignment."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from .contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage
from .grobid_service import ensure_grobid_service
from .pdf import parse_native_pdf_bytes

WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class GrobidBodyUnit:
    """One body heading or paragraph extracted from GROBID TEI."""

    text: str
    role: str


def parse_grobid_pdf_bytes(source_name: str, pdf_bytes: bytes) -> ParsedPdfDocument:
    """Parse a paper with GROBID semantics and PyMuPDF chunk locations."""

    native_document = parse_native_pdf_bytes(source_name=source_name, pdf_bytes=pdf_bytes)
    tei_xml = _fetch_grobid_tei(source_name=source_name, pdf_bytes=pdf_bytes)
    body_units = extract_grobid_body_units(tei_xml)
    if not body_units:
        raise ValueError("GROBID did not return body paragraphs for this PDF.")

    filtered_document = filter_parsed_document_to_grobid_body(
        parsed_document=native_document,
        body_units=body_units,
    )
    if not any(page.chunks for page in filtered_document.pages):
        raise ValueError("Could not align GROBID body paragraphs to PDF text chunks.")

    filtered_document.title = extract_grobid_title(tei_xml) or native_document.title
    filtered_document.metadata.update(
        {
            "grobid_body_units": len(body_units),
            "parser": "grobid+pymupdf",
        }
    )
    return filtered_document


def extract_grobid_title(tei_xml: str) -> str | None:
    """Extract the TEI title when present."""

    root = ET.fromstring(tei_xml)
    for element in root.iter():
        if _local_name(element.tag) == "title":
            title = _normalize_text(" ".join(element.itertext()))
            if title:
                return title
    return None


def extract_grobid_body_units(tei_xml: str) -> list[GrobidBodyUnit]:
    """Return headings and paragraphs from TEI body, excluding front and bibliography."""

    root = ET.fromstring(tei_xml)
    body = _find_first_by_local_name(root, "body")
    if body is None:
        return []

    units: list[GrobidBodyUnit] = []
    for element in body.iter():
        local_name = _local_name(element.tag)
        if local_name not in {"head", "p"}:
            continue

        text = _normalize_text(" ".join(element.itertext()))
        if text:
            units.append(GrobidBodyUnit(text=text, role="heading" if local_name == "head" else "paragraph"))
    return units


def filter_parsed_document_to_grobid_body(
    parsed_document: ParsedPdfDocument,
    body_units: list[GrobidBodyUnit],
) -> ParsedPdfDocument:
    """Keep only chunks that align to GROBID body headings and paragraphs."""

    ordered_chunks = [
        chunk
        for page in sorted(parsed_document.pages, key=lambda item: item.page_number)
        for chunk in sorted(page.chunks, key=lambda item: (item.y, item.x))
    ]
    selected_chunk_ids = _align_body_units_to_chunks(body_units=body_units, chunks=ordered_chunks)

    pages: list[ParsedPdfPage] = []
    for page in parsed_document.pages:
        page_chunks = [chunk for chunk in page.chunks if chunk.chunk_id in selected_chunk_ids]
        pages.append(
            ParsedPdfPage(
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                chunks=page_chunks,
            )
        )

    metadata = dict(parsed_document.metadata)
    metadata["excluded_regions"] = ["front", "bibliography"]
    return ParsedPdfDocument(title=parsed_document.title, pages=pages, metadata=metadata)


def _fetch_grobid_tei(source_name: str, pdf_bytes: bytes) -> str:
    """Request TEI XML through the backend-managed GROBID service."""

    grobid_url = ensure_grobid_service()
    endpoint = f"{grobid_url}/api/processFulltextDocument"
    files = {"input": (source_name, pdf_bytes, "application/pdf")}
    data = {
        "consolidateHeader": "0",
        "consolidateCitations": "0",
        "includeRawAffiliations": "1",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(endpoint, files=files, data=data)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise ValueError(f"Unable to parse PDF with GROBID at {grobid_url}: {error}") from error

    return response.text


def _align_body_units_to_chunks(
    body_units: list[GrobidBodyUnit],
    chunks: list[ParsedPdfChunk],
) -> set[str]:
    """Greedily align TEI body units to available parsed chunks."""

    selected_chunk_ids: set[str] = set()
    cursor = 0
    for unit in body_units:
        match = _find_matching_chunk_run(unit.text, chunks=chunks, start_index=cursor)
        if not match:
            continue

        selected_chunk_ids.update(chunk.chunk_id for _, chunk in match)
        cursor = match[-1][0] + 1

    return selected_chunk_ids


def _find_matching_chunk_run(
    unit_text: str,
    chunks: list[ParsedPdfChunk],
    start_index: int,
) -> list[tuple[int, ParsedPdfChunk]]:
    """Find a short chunk run that best covers one TEI body unit."""

    unit_tokens = set(_tokenize(unit_text))
    if not unit_tokens:
        return []

    best_match: list[tuple[int, ParsedPdfChunk]] = []
    best_coverage = 0.0

    for index in range(start_index, len(chunks)):
        selected: list[tuple[int, ParsedPdfChunk]] = []
        covered_tokens: set[str] = set()

        for chunk_index in range(index, min(index + 8, len(chunks))):
            chunk = chunks[chunk_index]
            chunk_tokens = set(_tokenize(chunk.text))
            if not chunk_tokens:
                continue

            overlap = chunk_tokens & unit_tokens
            overlap_ratio = len(overlap) / max(len(chunk_tokens), 1)
            if overlap_ratio < 0.45 and selected:
                break
            if overlap_ratio < 0.45:
                continue

            selected.append((chunk_index, chunk))
            covered_tokens.update(overlap)
            coverage = len(covered_tokens) / len(unit_tokens)
            if coverage >= 0.80:
                return selected

        coverage = len(covered_tokens) / len(unit_tokens)
        if selected and coverage > best_coverage:
            best_match = selected
            best_coverage = coverage

    return best_match if best_coverage >= 0.45 else []


def _tokenize(text: str) -> list[str]:
    """Normalize text for robust paragraph alignment."""

    return WORD_RE.findall(text.lower())


def _find_first_by_local_name(root: ET.Element, local_name: str) -> ET.Element | None:
    """Find the first element with a namespace-independent local name."""

    for element in root.iter():
        if _local_name(element.tag) == local_name:
            return element
    return None


def _local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""

    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_text(text: str) -> str:
    """Collapse whitespace in TEI text."""

    return " ".join(text.split())

"""Semantic parsing helpers for zoomable document construction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from ..models import PdfBlock, ZoomableNode
from ..representations import build_default_block_representations
from .contracts import ParsedPdfChunk, ParsedPdfDocument

NUMBERED_HEADING_RE = re.compile(r"^(?P<prefix>\d+(?:\.\d+)*|[IVXLC]+|[A-Z])[.)]?\s+\S")


@dataclass(slots=True)
class LayoutParagraph:
    """A paragraph-like unit built from one or more raw text chunks."""

    page_number: int
    text: str
    chunks: list[ParsedPdfChunk] = field(default_factory=list)
    font_size: float | None = None
    is_bold: bool = False
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass(slots=True)
class SemanticDocumentPlan:
    """The semantic paragraph blocks and tree derived from parsed PDF chunks."""

    title: str
    blocks: list[PdfBlock] = field(default_factory=list)
    zoomable_document: ZoomableNode | None = None
    chunk_to_block_ids: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


def build_semantic_document_plan(
    parsed_document: ParsedPdfDocument,
    source_name: str,
) -> SemanticDocumentPlan:
    """Build the semantic paragraph blocks and zoomable outline."""

    if parsed_document.metadata.get("llm_semantic_groups"):
        return _build_llm_semantic_document_plan(parsed_document=parsed_document, source_name=source_name)

    body_font_size = _estimate_body_font_size(
        chunk.font_size
        for page in parsed_document.pages
        for chunk in page.chunks
    )
    layout_paragraphs = _build_layout_paragraphs(parsed_document, body_font_size=body_font_size)
    layout_body_font_size = _estimate_body_font_size(paragraph.font_size for paragraph in layout_paragraphs) or body_font_size
    document_title, content_paragraphs = _extract_document_title(
        layout_paragraphs=layout_paragraphs,
        source_name=source_name,
        fallback_title=parsed_document.title,
        body_font_size=layout_body_font_size,
    )

    root = ZoomableNode(
        node_id="document-root",
        title=document_title,
        node_type="document",
        level=0,
    )
    blocks: list[PdfBlock] = []
    chunk_to_block_ids: dict[str, list[str]] = {}
    page_block_counters: dict[int, int] = {}
    current_section: ZoomableNode | None = None
    current_subsection: ZoomableNode | None = None

    for paragraph in content_paragraphs:
        role = _classify_layout_paragraph(paragraph, body_font_size=layout_body_font_size)

        if role == "section":
            current_section = ZoomableNode(
                node_id=f"section-{len(root.children) + 1:03d}",
                title=paragraph.text,
                node_type="section",
                level=1,
                page_number=paragraph.page_number,
                text=paragraph.text,
            )
            root.children.append(current_section)
            current_subsection = None
            continue

        if role == "subsection":
            current_section = current_section or _ensure_default_section(root)
            current_subsection = ZoomableNode(
                node_id=f"{current_section.node_id}-subsection-{len(current_section.children) + 1:03d}",
                title=paragraph.text,
                node_type="subsection",
                level=2,
                page_number=paragraph.page_number,
                text=paragraph.text,
            )
            current_section.children.append(current_subsection)
            continue

        current_section = current_section or _ensure_default_section(root)
        parent = current_subsection or current_section
        page_block_counters[paragraph.page_number] = page_block_counters.get(paragraph.page_number, 0) + 1
        block_id = f"block-{paragraph.page_number:03d}-{page_block_counters[paragraph.page_number]:04d}"
        section_path = [node.title for node in (current_section, current_subsection) if node is not None]
        block = PdfBlock(
            block_id=block_id,
            page_number=paragraph.page_number,
            text=paragraph.text,
            chunk_ids=[chunk.chunk_id for chunk in paragraph.chunks],
            section_path=section_path,
            representations=build_default_block_representations(paragraph.text),
        )
        blocks.append(block)
        for chunk in paragraph.chunks:
            chunk_to_block_ids.setdefault(chunk.chunk_id, []).append(block_id)

        parent.children.append(
            ZoomableNode(
                node_id=f"node-{block_id}",
                title=_short_title(paragraph.text),
                node_type="paragraph",
                level=3 if current_subsection is not None else 2,
                page_number=paragraph.page_number,
                block_id=block_id,
                text=paragraph.text,
                chunk_ids=list(block.chunk_ids),
            )
        )

    return SemanticDocumentPlan(
        title=document_title,
        blocks=blocks,
        zoomable_document=root,
        chunk_to_block_ids=chunk_to_block_ids,
        metadata=_build_semantic_metadata(parsed_document=parsed_document, paragraph_count=len(blocks)),
    )


def _build_layout_paragraphs(
    parsed_document: ParsedPdfDocument,
    body_font_size: float,
) -> list[LayoutParagraph]:
    """Merge raw text chunks into paragraph-like layout units."""

    if parsed_document.metadata.get("presegmented_chunks"):
        return [
            _build_layout_paragraph([chunk])
            for page in sorted(parsed_document.pages, key=lambda item: item.page_number)
            for chunk in page.chunks
        ]

    layout_paragraphs: list[LayoutParagraph] = []

    for page in parsed_document.pages:
        columns = _cluster_chunks_into_columns(page.chunks)
        for column in columns:
            ordered_chunks = sorted(column, key=lambda chunk: (_chunk_box(chunk)["top"], _chunk_box(chunk)["x0"]))
            groups: list[list[ParsedPdfChunk]] = []

            for chunk in ordered_chunks:
                if not groups:
                    groups.append([chunk])
                    continue

                previous_group = groups[-1]
                if _should_merge_chunk(previous_group, chunk, body_font_size=body_font_size):
                    previous_group.append(chunk)
                    continue

                groups.append([chunk])

            for group in groups:
                layout_paragraphs.append(_build_layout_paragraph(group))

    return layout_paragraphs


def _build_semantic_metadata(parsed_document: ParsedPdfDocument, paragraph_count: int) -> dict[str, object]:
    """Describe the semantic pipeline used for this parsed document."""

    semantic_source = parsed_document.metadata.get("semantic_source")
    if semantic_source == "opendataloader-llm":
        semantic_parser = "opendataloader-llm-semantic-v1"
    elif semantic_source == "opendataloader-json":
        semantic_parser = "opendataloader-semantic-json-v1"
    elif parsed_document.metadata.get("presegmented_chunks"):
        semantic_parser = "provider-presegmented-v1"
    else:
        semantic_parser = "heuristic-layout-v1"

    metadata: dict[str, object] = {
        "semantic_parser": semantic_parser,
        "paragraph_count": paragraph_count,
    }
    if semantic_source:
        metadata["semantic_source"] = semantic_source
    return metadata


def _build_llm_semantic_document_plan(
    parsed_document: ParsedPdfDocument,
    source_name: str,
) -> SemanticDocumentPlan:
    """Build a semantic tree from LLM paragraph groups."""

    semantic_groups = parsed_document.metadata.get("llm_semantic_groups") or {}
    chunks_by_id = {
        chunk.chunk_id: chunk
        for page in parsed_document.pages
        for chunk in page.chunks
    }
    document_title = str(semantic_groups.get("title") or parsed_document.title or source_name).strip() or source_name
    root = ZoomableNode(node_id="document-root", title=document_title, node_type="document", level=0)
    section_nodes: dict[tuple[str, ...], ZoomableNode] = {}
    blocks: list[PdfBlock] = []
    chunk_to_block_ids: dict[str, list[str]] = {}

    for index, item in enumerate(semantic_groups.get("paragraphs", []), start=1):
        chunk_ids = [chunk_id for chunk_id in item.get("chunk_ids", []) if chunk_id in chunks_by_id]
        chunks = [chunks_by_id[chunk_id] for chunk_id in chunk_ids]
        if not chunks:
            continue

        section_path = [part for part in item.get("section_path", []) if str(part).strip()] or ["Body"]
        parent = _ensure_section_path(root=root, section_path=section_path, section_nodes=section_nodes)
        page_number = chunks[0].page_number
        block_id = _safe_block_id(str(item.get("paragraph_id") or ""), fallback=f"block-{page_number:03d}-{index:04d}")
        text = " ".join(chunk.text.strip() for chunk in chunks if chunk.text.strip())
        block = PdfBlock(
            block_id=block_id,
            page_number=page_number,
            text=text,
            chunk_ids=chunk_ids,
            section_path=section_path,
            representations=[],
        )
        blocks.append(block)
        for chunk_id in chunk_ids:
            chunk_to_block_ids.setdefault(chunk_id, []).append(block_id)

        parent.children.append(
            ZoomableNode(
                node_id=f"node-{block_id}",
                title=_short_title(text),
                node_type="paragraph",
                level=len(section_path) + 1,
                page_number=page_number,
                block_id=block_id,
                text=text,
                chunk_ids=list(chunk_ids),
            )
        )

    return SemanticDocumentPlan(
        title=document_title,
        blocks=blocks,
        zoomable_document=root,
        chunk_to_block_ids=chunk_to_block_ids,
        metadata=_build_semantic_metadata(parsed_document=parsed_document, paragraph_count=len(blocks)),
    )


def _ensure_section_path(
    root: ZoomableNode,
    section_path: list[str],
    section_nodes: dict[tuple[str, ...], ZoomableNode],
) -> ZoomableNode:
    parent = root
    for depth, title in enumerate(section_path, start=1):
        key = tuple(section_path[:depth])
        node = section_nodes.get(key)
        if node is None:
            node_type = "section" if depth == 1 else "subsection"
            node = ZoomableNode(
                node_id=f"{node_type}-{len(section_nodes) + 1:03d}",
                title=title,
                node_type=node_type,
                level=depth,
                text=title,
            )
            parent.children.append(node)
            section_nodes[key] = node
        parent = node
    return parent


def _safe_block_id(value: str, fallback: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in value).strip("-")
    return safe or fallback


def _build_layout_paragraph(chunks: list[ParsedPdfChunk]) -> LayoutParagraph:
    """Build one paragraph-like unit from a chunk group."""

    box = _chunk_group_box(chunks)
    font_size = _estimate_body_font_size(chunk.font_size for chunk in chunks)
    text = " ".join(chunk.text.strip() for chunk in chunks if chunk.text.strip())
    return LayoutParagraph(
        page_number=chunks[0].page_number,
        text=text,
        chunks=list(chunks),
        font_size=font_size,
        is_bold=any(bool(chunk.is_bold) for chunk in chunks),
        x=box["x0"],
        y=box["top"],
        width=box["width"],
        height=box["height"],
    )


def _extract_document_title(
    layout_paragraphs: list[LayoutParagraph],
    source_name: str,
    fallback_title: str,
    body_font_size: float,
) -> tuple[str, list[LayoutParagraph]]:
    """Promote a large top-of-page heading to the document title when appropriate."""

    if not layout_paragraphs:
        return fallback_title or source_name, []

    metadata_title = (fallback_title or "").strip()
    title_candidate = metadata_title if metadata_title and metadata_title != source_name else None
    inferred_paragraph = None
    first_page_candidates = [
        paragraph
        for paragraph in layout_paragraphs
        if paragraph.page_number == 1
        and paragraph.y <= 0.30
        and len(paragraph.text) <= 220
        and not NUMBERED_HEADING_RE.match(paragraph.text)
    ]
    if first_page_candidates:
        strongest_candidate = max(
            first_page_candidates,
            key=lambda paragraph: ((paragraph.font_size or 0.0), -paragraph.y),
        )
        next_font_size = max(
            (paragraph.font_size or 0.0)
            for paragraph in first_page_candidates
            if paragraph is not strongest_candidate
        ) if len(first_page_candidates) > 1 else 0.0
        candidate_font_size = strongest_candidate.font_size or 0.0
        if candidate_font_size >= max(body_font_size * 1.20, next_font_size + 1.5):
            inferred_paragraph = strongest_candidate

    if title_candidate:
        return title_candidate, layout_paragraphs
    if inferred_paragraph is not None:
        return inferred_paragraph.text, [paragraph for paragraph in layout_paragraphs if paragraph is not inferred_paragraph]
    return source_name, layout_paragraphs


def _classify_layout_paragraph(paragraph: LayoutParagraph, body_font_size: float) -> str:
    """Classify a layout paragraph as a heading or semantic paragraph."""

    text = paragraph.text.strip()
    if not text:
        return "paragraph"

    heading_levels = [
        chunk.heading_level
        for chunk in paragraph.chunks
        if chunk.semantic_type == "heading" and chunk.heading_level is not None
    ]
    if heading_levels:
        return "section" if min(heading_levels) <= 1 else "subsection"

    if any(chunk.semantic_type == "heading" for chunk in paragraph.chunks):
        return "section"

    match = NUMBERED_HEADING_RE.match(text)
    if match:
        depth = match.group("prefix").count(".") + 1 if "." in match.group("prefix") else 1
        return "section" if depth <= 1 else "subsection"

    if _looks_like_heading_text(text) and (paragraph.font_size or 0.0) >= body_font_size * 1.30:
        return "section"

    if _looks_like_heading_text(text) and (
        (paragraph.font_size or 0.0) >= body_font_size * 1.12 or paragraph.is_bold
    ):
        return "subsection"

    return "paragraph"


def _looks_like_heading_text(text: str) -> bool:
    """Return whether a text block resembles a heading."""

    compact = " ".join(text.split())
    word_count = len(compact.split())
    if not compact or len(compact) > 140 or word_count > 14:
        return False
    if compact.endswith((".", "?", "!")):
        return False
    return True


def _should_merge_chunk(
    previous_group: list[ParsedPdfChunk],
    current_chunk: ParsedPdfChunk,
    body_font_size: float,
) -> bool:
    """Return whether a raw chunk should extend the current paragraph group."""

    previous_chunk = previous_group[-1]
    if _looks_like_heading_chunk(previous_chunk, body_font_size) or _looks_like_heading_chunk(current_chunk, body_font_size):
        return False

    previous_box = _chunk_group_box(previous_group)
    current_box = _chunk_box(current_chunk)
    vertical_gap = max(current_box["top"] - previous_box["bottom"], 0.0)
    left_delta = abs(current_box["x0"] - previous_box["x0"])
    center_delta = abs(_box_center_x(current_box) - _box_center_x(previous_box))
    overlap_ratio = _horizontal_overlap_ratio(current_box, previous_box)
    font_delta = _font_size_delta(previous_chunk.font_size, current_chunk.font_size)
    gap_limit = max(previous_box["height"] * 2.0, current_box["height"] * 2.0, 0.04)

    return (
        vertical_gap <= gap_limit
        and font_delta <= 2.2
        and (
            overlap_ratio >= 0.25
            or left_delta <= 0.05
            or center_delta <= 0.06
        )
    )


def _looks_like_heading_chunk(chunk: ParsedPdfChunk, body_font_size: float) -> bool:
    """Return whether a raw chunk likely behaves like a heading block."""

    if chunk.semantic_type == "heading":
        return True

    text = chunk.text.strip()
    if NUMBERED_HEADING_RE.match(text):
        return True
    if not _looks_like_heading_text(text):
        return False
    if bool(chunk.is_bold):
        return True
    return (chunk.font_size or 0.0) >= body_font_size * 1.12


def _cluster_chunks_into_columns(chunks: list[ParsedPdfChunk]) -> list[list[ParsedPdfChunk]]:
    """Cluster raw text chunks into stable reading columns."""

    columns: list[list[ParsedPdfChunk]] = []
    ordered_chunks = sorted(chunks, key=lambda chunk: (_chunk_box(chunk)["top"], _chunk_box(chunk)["x0"]))

    for chunk in ordered_chunks:
        box = _chunk_box(chunk)
        placed = False

        for column in columns:
            reference_box = _chunk_group_box(column)
            center_delta = abs(_box_center_x(box) - _box_center_x(reference_box))
            left_delta = abs(box["x0"] - reference_box["x0"])
            overlap_ratio = _horizontal_overlap_ratio(box, reference_box)

            if overlap_ratio >= 0.35 or center_delta <= 0.08 or left_delta <= 0.05:
                column.append(chunk)
                placed = True
                break

        if not placed:
            columns.append([chunk])

    return sorted(columns, key=lambda column: _chunk_group_box(column)["x0"])


def _ensure_default_section(root: ZoomableNode) -> ZoomableNode:
    """Return the default body section, creating it if missing."""

    if root.children and root.children[-1].title == "Body" and root.children[-1].node_type == "section":
        return root.children[-1]

    section = ZoomableNode(
        node_id=f"section-{len(root.children) + 1:03d}",
        title="Body",
        node_type="section",
        level=1,
    )
    root.children.append(section)
    return section


def _estimate_body_font_size(values) -> float:
    """Return a stable body-font estimate from available font sizes."""

    sizes = sorted(float(value) for value in values if value is not None)
    if not sizes:
        return 12.0
    return median(sizes)


def _font_size_delta(first: float | None, second: float | None) -> float:
    """Return the absolute font-size delta for two values."""

    if first is None or second is None:
        return 0.0
    return abs(float(first) - float(second))


def _short_title(text: str, limit: int = 56) -> str:
    """Return a stable short label for a paragraph node."""

    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _chunk_box(chunk: ParsedPdfChunk) -> dict[str, float]:
    """Return a normalized box for one parsed chunk."""

    return {
        "x0": chunk.x,
        "x1": chunk.x + chunk.width,
        "top": chunk.y,
        "bottom": chunk.y + chunk.height,
        "width": chunk.width,
        "height": chunk.height,
    }


def _chunk_group_box(chunks: list[ParsedPdfChunk]) -> dict[str, float]:
    """Return one normalized bounding box for a chunk group."""

    x0 = min(chunk.x for chunk in chunks)
    x1 = max(chunk.x + chunk.width for chunk in chunks)
    top = min(chunk.y for chunk in chunks)
    bottom = max(chunk.y + chunk.height for chunk in chunks)
    return {
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
        "width": x1 - x0,
        "height": bottom - top,
    }


def _box_center_x(box: dict[str, float]) -> float:
    """Return the horizontal center of a normalized box."""

    return (box["x0"] + box["x1"]) / 2.0


def _horizontal_overlap_ratio(first: dict[str, float], second: dict[str, float]) -> float:
    """Return the horizontal overlap ratio relative to the narrower box."""

    overlap = max(0.0, min(first["x1"], second["x1"]) - max(first["x0"], second["x0"]))
    narrower = max(min(first["width"], second["width"]), 1e-6)
    return overlap / narrower

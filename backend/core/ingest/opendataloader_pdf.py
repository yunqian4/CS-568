"""OpenDataLoader PDF provider adapter."""

from __future__ import annotations

import inspect
import json
import os
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import fitz

from .contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage

TEXT_ELEMENT_TYPES = {"heading", "paragraph", "caption", "list item"}
TRUE_VALUES = {"1", "true", "yes", "on"}


def parse_opendataloader_pdf_bytes(source_name: str, pdf_bytes: bytes) -> ParsedPdfDocument:
    """Extract semantic elements and bounding boxes with OpenDataLoader PDF."""

    ensure_java_on_path()
    opendataloader_pdf = _import_opendataloader_pdf()
    page_sizes = _extract_page_sizes(pdf_bytes)

    with TemporaryDirectory() as temp_root:
        temp_dir = Path(temp_root)
        input_path = temp_dir / _safe_pdf_name(source_name)
        output_dir = temp_dir / "output"
        input_path.write_bytes(pdf_bytes)
        output_dir.mkdir()

        _convert_pdf(opendataloader_pdf.convert, input_path=input_path, output_dir=output_dir)
        document_json = _load_document_json(output_dir=output_dir, input_stem=input_path.stem)

    return build_parsed_document_from_opendataloader_json(
        source_name=source_name,
        document_json=document_json,
        page_sizes=page_sizes,
    )


def build_parsed_document_from_opendataloader_json(
    source_name: str,
    document_json: dict[str, Any],
    page_sizes: dict[int, tuple[float, float]],
) -> ParsedPdfDocument:
    """Map OpenDataLoader JSON elements into the shared parser contract."""

    pages = {
        page_number: ParsedPdfPage(page_number=page_number, width=size[0], height=size[1])
        for page_number, size in sorted(page_sizes.items())
    }
    chunk_counters: dict[int, int] = {}

    for element in _iter_text_elements(document_json):
        page_number = _int_value(element.get("page number"))
        if page_number is None or page_number not in pages:
            continue

        text = _normalize_text(str(element.get("content", "")))
        bbox = _parse_bounding_box(element.get("bounding box"))
        if not text or bbox is None:
            continue

        page = pages[page_number]
        normalized_box = _normalize_pdf_box(bbox=bbox, page_width=page.width, page_height=page.height)
        if normalized_box is None:
            continue

        element_type = str(element.get("type", "")).lower()
        font_name = _optional_string(element.get("font"))
        chunk_counters[page_number] = chunk_counters.get(page_number, 0) + 1
        page.chunks.append(
            ParsedPdfChunk(
                chunk_id=f"opendataloader-{page_number:03d}-{chunk_counters[page_number]:04d}",
                page_number=page_number,
                text=text,
                x=normalized_box["x"],
                y=normalized_box["y"],
                width=normalized_box["width"],
                height=normalized_box["height"],
                font_size=_float_value(element.get("font size")),
                font_name=font_name,
                is_bold=element_type == "heading" or bool(font_name and "bold" in font_name.lower()),
                semantic_type=element_type,
                heading_level=_int_value(element.get("heading level")),
            )
        )

    title = _optional_string(document_json.get("title")) or source_name
    return ParsedPdfDocument(
        title=title,
        pages=list(pages.values()),
        metadata={
            "parser": "opendataloader-pdf",
            "presegmented_chunks": True,
            "semantic_source": "opendataloader-json",
        },
    )


def ensure_java_on_path() -> None:
    """Ensure OpenDataLoader can launch the Java runtime it depends on."""

    if shutil.which("java"):
        return

    for java_path in _java_candidates():
        if not java_path.exists():
            continue

        java_bin_dir = str(java_path.parent)
        os.environ["PATH"] = f"{java_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ.setdefault("JAVA_HOME", str(java_path.parent.parent))
        if shutil.which("java"):
            return

    raise ValueError(
        "The OpenDataLoader provider requires Java 11+ on PATH. "
        "Install Java or set JAVA_HOME to a JDK/JRE directory that contains bin\\java.exe."
    )


def _import_opendataloader_pdf():
    try:
        import opendataloader_pdf
    except ImportError as error:
        raise ValueError(
            "The OpenDataLoader provider requires the opendataloader-pdf package "
            "and Java 11+ on PATH. Install it with: "
            "backend\\.venv\\Scripts\\python.exe -m pip install opendataloader-pdf"
        ) from error
    return opendataloader_pdf


def _convert_pdf(convert, input_path: Path, output_dir: Path) -> None:
    """Call OpenDataLoader while tolerating small API differences across versions."""

    kwargs: dict[str, Any] = {
        "input_path": [str(input_path)],
        "output_dir": str(output_dir),
        "format": "json",
    }
    if _supports_parameter(convert, "quiet"):
        kwargs["quiet"] = True
    if _supports_parameter(convert, "use_struct_tree") and _use_struct_tree_enabled():
        kwargs["use_struct_tree"] = True

    try:
        convert(**kwargs)
    except FileNotFoundError as error:
        raise ValueError(
            "OpenDataLoader PDF could not launch Java. "
            "Install Java 11+ or set JAVA_HOME to a JDK/JRE directory that contains bin\\java.exe."
        ) from error
    except TypeError as error:
        if "unexpected keyword" not in str(error):
            raise
        kwargs.pop("quiet", None)
        kwargs.pop("use_struct_tree", None)
        convert(**kwargs)
    except Exception as error:
        raise ValueError(f"OpenDataLoader PDF conversion failed: {error}") from error


def _supports_parameter(function, name: str) -> bool:
    signature = inspect.signature(function)
    if name in signature.parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _java_candidates() -> list[Path]:
    candidates: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / "java.exe")

    for root_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_name)
        if not root:
            continue

        for pattern in (
            "Eclipse Adoptium/*/bin/java.exe",
            "Java/*/bin/java.exe",
            "Microsoft/jdk-*/bin/java.exe",
            "Microsoft/*/bin/java.exe",
        ):
            candidates.extend(sorted(Path(root).glob(pattern), reverse=True))

    return candidates


def _load_document_json(output_dir: Path, input_stem: str) -> dict[str, Any]:
    json_files = sorted(path for path in output_dir.rglob("*.json") if path.is_file())
    preferred = [path for path in json_files if path.stem == input_stem]
    for path in preferred + [path for path in json_files if path not in preferred]:
        try:
            document_json = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document_json, dict) and "kids" in document_json:
            return document_json
    raise ValueError("OpenDataLoader PDF did not produce a document JSON file.")


def _iter_text_elements(node: Any):
    if isinstance(node, dict):
        node_type = str(node.get("type", "")).lower()
        if node_type in TEXT_ELEMENT_TYPES and node.get("content") and not bool(node.get("hidden text")):
            yield node

        for child in _iter_child_nodes(node):
            yield from _iter_text_elements(child)
        return

    if isinstance(node, list):
        for child in node:
            yield from _iter_text_elements(child)


def _iter_child_nodes(node: dict[str, Any]):
    for key in ("kids", "list items", "cells"):
        children = node.get(key)
        if isinstance(children, list):
            yield from children
    rows = node.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("cells"), list):
                yield from row["cells"]


def _extract_page_sizes(pdf_bytes: bytes) -> dict[int, tuple[float, float]]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return {
            page_index + 1: (float(page.rect.width or 1.0), float(page.rect.height or 1.0))
            for page_index, page in enumerate(document)
        }
    finally:
        document.close()


def _parse_bounding_box(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        left, bottom, right, top = value
        return (float(left), float(bottom), float(right), float(top))
    if isinstance(value, dict):
        left = value.get("left", value.get("x1", value.get("l")))
        bottom = value.get("bottom", value.get("y1", value.get("b")))
        right = value.get("right", value.get("x2", value.get("r")))
        top = value.get("top", value.get("y2", value.get("t")))
        if None not in (left, bottom, right, top):
            return (float(left), float(bottom), float(right), float(top))
    return None


def _normalize_pdf_box(
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> dict[str, float] | None:
    left, bottom, right, top = bbox
    if right <= left or top <= bottom or page_width <= 0 or page_height <= 0:
        return None

    x = _clamp(left / page_width)
    y = _clamp(1.0 - (top / page_height))
    return {
        "x": x,
        "y": y,
        "width": _clamp((right - left) / page_width),
        "height": _clamp((top - bottom) / page_height),
    }


def _safe_pdf_name(source_name: str) -> str:
    stem = Path(source_name).stem or "document"
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip(".-") or "document"
    return f"{safe_stem}.pdf"


def _use_struct_tree_enabled() -> bool:
    value = os.environ.get("OPENDATALOADER_USE_STRUCT_TREE")
    return value is None or value.lower() in TRUE_VALUES


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)

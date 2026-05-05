"""LLM semantic grouping for OpenDataLoader parser output."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..representations.llm import (
    DEFAULT_MODEL,
    _bounded_int,
    _extract_output_text,
    _post_openai_response,
)
from .contracts import ParsedPdfDocument

SEMANTIC_WINDOW_CHUNK_LIMIT = 12
SEMANTIC_WINDOW_CHAR_LIMIT = 6000
SEMANTIC_MAX_OUTPUT_TOKENS = 8192
SENTENCE_END_RE = re.compile(r"""[.!?]["')\]]*$""")
CONTINUATION_START_RE = re.compile(r"""^[a-z,;:)\]\-]""")


@dataclass(slots=True)
class LlmSemanticConfig:
    """Settings for LLM semantic grouping."""

    enabled: bool = False
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    artifact_dir: Path | None = None
    timeout_seconds: float = 300.0
    request_retries: int = 3
    endpoint: str = "https://api.openai.com/v1/responses"
    window_chunk_limit: int = SEMANTIC_WINDOW_CHUNK_LIMIT
    window_char_limit: int = SEMANTIC_WINDOW_CHAR_LIMIT
    max_output_tokens: int = SEMANTIC_MAX_OUTPUT_TOKENS

    @classmethod
    def from_values(
        cls,
        *,
        enabled: bool = False,
        api_key: str | None = None,
        model: str | None = None,
        artifact_dir: Path | None = None,
    ) -> "LlmSemanticConfig":
        """Build validated semantic grouping settings."""

        return cls(
            enabled=enabled,
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            model=(model or os.environ.get("OPENAI_REPRESENTATION_MODEL") or DEFAULT_MODEL).strip(),
            artifact_dir=artifact_dir,
            request_retries=_bounded_int(os.environ.get("OPENAI_REQUEST_RETRIES"), 3, minimum=0, maximum=5),
        )


def apply_llm_semantic_grouping(parsed_document: ParsedPdfDocument, config: LlmSemanticConfig) -> dict[str, Any]:
    """Attach validated LLM semantic groups to parser metadata."""

    if not config.enabled:
        return {"enabled": False}

    api_key = config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("OpenDataLoader LLM semantic parsing requires a user API key or OPENAI_API_KEY.")

    semantic_input = _semantic_input(parsed_document)
    semantic_groups, window_artifacts = _call_semantic_windows(
        api_key=api_key,
        config=config,
        semantic_input=semantic_input,
    )

    parsed_document.metadata["llm_semantic_groups"] = semantic_groups
    parsed_document.metadata["semantic_source"] = "opendataloader-llm"
    parsed_document.metadata["presegmented_chunks"] = True

    if config.artifact_dir:
        llm_dir = config.artifact_dir / "llm"
        llm_dir.mkdir(parents=True, exist_ok=True)
        _write_json(llm_dir / "semantic-input.json", semantic_input)
        _write_json(llm_dir / "semantic.json", semantic_groups)
        _write_window_artifacts(llm_dir=llm_dir, window_artifacts=window_artifacts)

    return {
        "enabled": True,
        "model": config.model,
        "key_source": "user" if config.api_key else "default",
        "paragraph_count": len(semantic_groups.get("paragraphs", [])),
        "ignored_chunk_count": len(semantic_groups.get("ignored_chunk_ids", [])),
        "semantic_window_count": len(window_artifacts),
    }


def _semantic_input(parsed_document: ParsedPdfDocument) -> dict[str, Any]:
    chunks = []
    reading_order = 0
    for page in sorted(parsed_document.pages, key=lambda item: item.page_number):
        for chunk in page.chunks:
            chunks.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "reading_order": reading_order,
                    "page_number": chunk.page_number,
                    "type": chunk.semantic_type or "text",
                    "heading_level": chunk.heading_level,
                    "text": chunk.text,
                    "bbox": {
                        "x": chunk.x,
                        "y": chunk.y,
                        "width": chunk.width,
                        "height": chunk.height,
                    },
                }
            )
            reading_order += 1
    return {"title": parsed_document.title, "chunks": chunks}


def _call_semantic_windows(
    *,
    api_key: str,
    config: LlmSemanticConfig,
    semantic_input: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    window_artifacts: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    ignored_chunk_ids: set[str] = set()
    previous_section_path: list[str] = []
    title = str(semantic_input.get("title") or "").strip()

    windows = _build_semantic_windows(semantic_input, config=config)
    for window_index, chunks in enumerate(windows, start=1):
        window_input = _window_input(
            semantic_input=semantic_input,
            chunks=chunks,
            window_index=window_index,
            window_count=len(windows),
            previous_section_path=previous_section_path,
        )
        for actual_input, raw_output in _collect_window_outputs(
            api_key=api_key,
            config=config,
            window_input=window_input,
        ):
            semantic_groups = _validate_semantic_output(raw_output, semantic_input=actual_input)
            title = semantic_groups.get("title") or title
            for paragraph in semantic_groups["paragraphs"]:
                if not paragraph["section_path"] and previous_section_path:
                    paragraph["section_path"] = list(previous_section_path)
                paragraph["source_paragraph_id"] = paragraph.get("paragraph_id") or ""
                paragraph["paragraph_id"] = f"paragraph-{len(paragraphs) + 1:04d}"
                if paragraph["section_path"]:
                    previous_section_path = list(paragraph["section_path"])
                paragraphs.append(paragraph)
            ignored_chunk_ids.update(semantic_groups["ignored_chunk_ids"])
            window_artifacts.append({"input": actual_input, "output": semantic_groups})

    chunks_by_id = {str(chunk["chunk_id"]): chunk for chunk in semantic_input["chunks"]}
    paragraphs = _merge_sentence_continuation_paragraphs(paragraphs, chunks_by_id=chunks_by_id)
    used_chunk_ids = {chunk_id for paragraph in paragraphs for chunk_id in paragraph["chunk_ids"]}
    return (
        {
            "title": title,
            "ignored_chunk_ids": sorted(ignored_chunk_ids - used_chunk_ids),
            "paragraphs": paragraphs,
        },
        window_artifacts,
    )


def _build_semantic_windows(semantic_input: dict[str, Any], config: LlmSemanticConfig) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    current_window: list[dict[str, Any]] = []
    current_chars = 0
    current_page = None

    for chunk in semantic_input["chunks"]:
        chunk_text_len = len(str(chunk.get("text") or ""))
        should_split = (
            current_window
            and (
                len(current_window) >= max(config.window_chunk_limit, 1)
                or current_chars + chunk_text_len > max(config.window_char_limit, 1000)
                or (
                    current_page is not None
                    and chunk.get("page_number") != current_page
                    and len(current_window) >= max(config.window_chunk_limit // 2, 1)
                )
            )
        )
        if should_split:
            windows.append(current_window)
            current_window = []
            current_chars = 0

        current_window.append(chunk)
        current_chars += chunk_text_len
        current_page = chunk.get("page_number")

    if current_window:
        windows.append(current_window)
    return windows


def _window_input(
    *,
    semantic_input: dict[str, Any],
    chunks: list[dict[str, Any]],
    window_index: int,
    window_count: int,
    previous_section_path: list[str],
) -> dict[str, Any]:
    return {
        "title": semantic_input.get("title") or "",
        "window": {
            "index": window_index,
            "count": window_count,
            "previous_section_path": previous_section_path,
        },
        "chunks": chunks,
    }


def _collect_window_outputs(
    *,
    api_key: str,
    config: LlmSemanticConfig,
    window_input: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    try:
        return [(window_input, _call_semantic_llm(api_key=api_key, config=config, semantic_input=window_input))]
    except ValueError as error:
        chunks = list(window_input.get("chunks") or [])
        if len(chunks) <= 1 or not _is_max_output_token_error(error):
            raise

        midpoint = max(len(chunks) // 2, 1)
        outputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for retry_index, retry_chunks in enumerate((chunks[:midpoint], chunks[midpoint:]), start=1):
            retry_input = {
                **window_input,
                "window": {
                    **dict(window_input.get("window") or {}),
                    "retry_part": retry_index,
                },
                "chunks": retry_chunks,
            }
            outputs.extend(_collect_window_outputs(api_key=api_key, config=config, window_input=retry_input))
        return outputs


def _call_semantic_llm(
    *,
    api_key: str,
    config: LlmSemanticConfig,
    semantic_input: dict[str, Any],
) -> dict[str, Any]:
    payload = _post_openai_response(
        api_key=api_key,
        config=config,
        operation="OpenDataLoader LLM semantic parsing",
        body={
            "model": config.model,
            "instructions": _semantic_instructions(),
            "input": json.dumps(semantic_input, ensure_ascii=False),
            "max_output_tokens": config.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pdf_semantic_groups",
                    "strict": True,
                    "schema": _semantic_schema(),
                }
            },
        },
    )
    try:
        return json.loads(_extract_output_text(payload))
    except json.JSONDecodeError as error:
        raise ValueError("OpenAI returned non-JSON semantic parsing output.") from error


def _semantic_instructions() -> str:
    """Return the prompt used to group OpenDataLoader chunks into paragraphs."""

    return (
        "Group OpenDataLoader PDF chunks into a semantic document outline. "
        "Use only supplied chunk IDs. Do not invent IDs or geometry. "
        "Chunks are already in provider reading_order; preserve that order when grouping and ordering paragraphs. "
        "Do not reorder chunks by bounding boxes. "
        "Each paragraph item may and usually should contain multiple chunk_ids when adjacent chunks are part of the same paragraph. "
        "Before ending a paragraph, check whether its combined text is sentence-complete. "
        "If the last sentence is incomplete, hyphenated, or naturally continues into the next chunk, include the next chunk in the same paragraph. "
        "If the next chunk starts lowercase, with punctuation, or with a continuation phrase, treat it as continuation unless a heading, table, caption, or list boundary intervenes. "
        "Split only at real semantic paragraph boundaries, headings, tables, captions, or list-item boundaries. "
        "Return sections as paths and paragraph groups for body text. "
        "Put authors, affiliations, references, headers, footers, and non-reading artifacts in ignored_chunk_ids."
    )


def _semantic_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "ignored_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "paragraphs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "paragraph_id": {"type": "string"},
                        "chunk_ids": {"type": "array", "items": {"type": "string"}},
                        "section_path": {"type": "array", "items": {"type": "string"}},
                        "role": {"type": "string"},
                    },
                    "required": ["paragraph_id", "chunk_ids", "section_path", "role"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "ignored_chunk_ids", "paragraphs"],
        "additionalProperties": False,
    }


def _validate_semantic_output(output: dict[str, Any], *, semantic_input: dict[str, Any]) -> dict[str, Any]:
    known_chunk_ids = {str(chunk["chunk_id"]) for chunk in semantic_input["chunks"]}
    chunk_order = _chunk_order_map(semantic_input)
    used_chunk_ids: set[str] = set()
    paragraphs: list[dict[str, Any]] = []

    for item in output.get("paragraphs", []):
        chunk_ids = [str(chunk_id).strip() for chunk_id in item.get("chunk_ids", []) if str(chunk_id).strip()]
        unknown = [chunk_id for chunk_id in chunk_ids if chunk_id not in known_chunk_ids]
        if unknown:
            raise ValueError(f"LLM semantic parsing returned unknown chunk IDs: {', '.join(unknown)}")
        if not chunk_ids:
            continue

        chunk_ids = sorted(_dedupe_preserving_order(chunk_ids), key=lambda chunk_id: chunk_order[chunk_id])
        used_chunk_ids.update(chunk_ids)
        paragraphs.append(
            {
                "paragraph_id": _safe_id(item.get("paragraph_id")) or f"paragraph-{len(paragraphs) + 1:04d}",
                "source_paragraph_id": "",
                "chunk_ids": chunk_ids,
                "section_path": [str(part).strip() for part in item.get("section_path", []) if str(part).strip()],
                "role": str(item.get("role") or "body").strip().lower() or "body",
            }
        )

    paragraphs.sort(key=lambda paragraph: min(chunk_order[chunk_id] for chunk_id in paragraph["chunk_ids"]))
    ignored_chunk_ids = [str(chunk_id).strip() for chunk_id in output.get("ignored_chunk_ids", []) if str(chunk_id).strip()]
    unknown_ignored = [chunk_id for chunk_id in ignored_chunk_ids if chunk_id not in known_chunk_ids]
    if unknown_ignored:
        raise ValueError(f"LLM semantic parsing ignored unknown chunk IDs: {', '.join(unknown_ignored)}")

    return {
        "title": str(output.get("title") or semantic_input.get("title") or "").strip(),
        "ignored_chunk_ids": sorted(set(ignored_chunk_ids) - used_chunk_ids),
        "paragraphs": paragraphs,
    }


def _chunk_order_map(semantic_input: dict[str, Any]) -> dict[str, int]:
    """Return the provider reading order for semantic chunks."""

    return {
        str(chunk["chunk_id"]): int(chunk.get("reading_order", index))
        for index, chunk in enumerate(semantic_input["chunks"])
    }


def _merge_sentence_continuation_paragraphs(
    paragraphs: list[dict[str, Any]],
    *,
    chunks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge adjacent LLM paragraph groups when a sentence was split."""

    merged: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        if merged and _should_merge_sentence_continuation(merged[-1], paragraph, chunks_by_id=chunks_by_id):
            previous = merged[-1]
            previous["chunk_ids"] = _dedupe_preserving_order(previous["chunk_ids"] + paragraph["chunk_ids"])
            continue
        merged.append({**paragraph, "chunk_ids": list(paragraph["chunk_ids"])})
    return merged


def _should_merge_sentence_continuation(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    chunks_by_id: dict[str, dict[str, Any]],
) -> bool:
    if previous.get("section_path") != current.get("section_path"):
        return False
    if previous.get("role") != current.get("role"):
        return False

    previous_text = _paragraph_text(previous, chunks_by_id=chunks_by_id)
    current_text = _paragraph_text(current, chunks_by_id=chunks_by_id)
    if not previous_text or not current_text:
        return False

    if previous_text.rstrip().endswith("-"):
        return True
    if SENTENCE_END_RE.search(previous_text):
        return False
    return bool(CONTINUATION_START_RE.search(current_text.lstrip()))


def _paragraph_text(paragraph: dict[str, Any], *, chunks_by_id: dict[str, dict[str, Any]]) -> str:
    return " ".join(
        str(chunks_by_id.get(chunk_id, {}).get("text") or "").strip()
        for chunk_id in paragraph.get("chunk_ids", [])
        if str(chunks_by_id.get(chunk_id, {}).get("text") or "").strip()
    )


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_max_output_token_error(error: ValueError) -> bool:
    return "max_output_tokens" in str(error)


def _write_window_artifacts(llm_dir: Path, window_artifacts: list[dict[str, Any]]) -> None:
    windows_dir = llm_dir / "semantic-windows"
    windows_dir.mkdir(parents=True, exist_ok=True)
    for index, artifact in enumerate(window_artifacts, start=1):
        _write_json(windows_dir / f"window-{index:03d}-input.json", artifact["input"])
        _write_json(windows_dir / f"window-{index:03d}-output.json", artifact["output"])


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(character if character.isalnum() or character in "-_" else "-" for character in text).strip("-")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

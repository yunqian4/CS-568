"""LLM-backed block representation generation."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..models import PdfBlockRepresentation, PdfDocument

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5-nano"
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")


@dataclass(slots=True)
class LlmRepresentationConfig:
    """Runtime settings for LLM-generated block representations."""

    enabled: bool = False
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    keyword_min_words: int = 4
    summary_min_words: int = 35
    summary_word_ratio: float = 0.15
    max_keywords: int = 5
    batch_size: int = 12
    parallel_jobs: int = 4
    timeout_seconds: float = 60.0
    endpoint: str = OPENAI_RESPONSES_URL

    @classmethod
    def from_values(
        cls,
        *,
        enabled: bool | None = False,
        api_key: str | None = None,
        model: str | None = None,
        keyword_min_words: int | None = None,
        summary_min_words: int | None = None,
        summary_word_ratio: float | None = None,
        max_keywords: int | None = None,
    ) -> "LlmRepresentationConfig":
        """Build a validated config from request values and environment defaults."""

        return cls(
            enabled=bool(enabled),
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            model=(model or os.environ.get("OPENAI_REPRESENTATION_MODEL") or DEFAULT_MODEL).strip(),
            keyword_min_words=_positive_int(keyword_min_words, 4),
            summary_min_words=_positive_int(summary_min_words, 35),
            summary_word_ratio=_bounded_float(summary_word_ratio, 0.15, minimum=0.02, maximum=0.80),
            max_keywords=_positive_int(max_keywords, 5),
            parallel_jobs=_bounded_int(os.environ.get("OPENAI_REPRESENTATION_PARALLELISM"), 4, minimum=1, maximum=8),
        )


def enrich_document_representations(
    document: PdfDocument,
    config: LlmRepresentationConfig,
) -> dict[str, Any]:
    """Replace placeholder block representations with LLM-generated values."""

    if not config.enabled:
        return {"enabled": False}

    api_key = config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("LLM representation generation requires a user API key or OPENAI_API_KEY.")

    block_tasks = [_build_block_task(block, config) for block in document.blocks]
    eligible_tasks = [task for task in block_tasks if task["tasks"]]
    task_by_block_id = {task["block_id"]: task for task in block_tasks}
    generated_by_block_id: dict[str, dict[str, Any]] = {}

    for task in eligible_tasks:
        block_result: dict[str, Any] = {}
        for kind in task["tasks"]:
            block_result.update(
                _call_openai_representation(
                    api_key=api_key,
                    config=config,
                    task=task,
                    kind=kind,
                )
            )
        generated_by_block_id[task["block_id"]] = block_result

    generated_count = 0
    for block in document.blocks:
        task = task_by_block_id.get(block.block_id)
        generated = generated_by_block_id.get(block.block_id, {})
        block.representations = _representations_for_block(task=task, generated=generated, config=config)
        if block.representations:
            generated_count += 1

    return {
        "enabled": True,
        "model": config.model,
        "key_source": "user" if config.api_key else "default",
        "keyword_min_words": config.keyword_min_words,
        "summary_min_words": config.summary_min_words,
        "summary_word_ratio": config.summary_word_ratio,
        "max_keywords": config.max_keywords,
        "eligible_blocks": len(eligible_tasks),
        "generated_blocks": generated_count,
        "skipped_blocks": len(block_tasks) - len(eligible_tasks),
    }


def _build_block_task(block, config: LlmRepresentationConfig) -> dict[str, Any]:
    word_count = len(WORD_RE.findall(block.text))
    tasks: list[str] = []
    if word_count >= config.keyword_min_words:
        tasks.append("keywords")
    if word_count >= config.summary_min_words:
        tasks.append("summary")

    return {
        "block_id": block.block_id,
        "text": block.text,
        "word_count": word_count,
        "tasks": tasks,
        "max_keywords": config.max_keywords,
        "summary_target_words": _summary_target_words(word_count, config) if "summary" in tasks else 0,
    }


def _summary_target_words(word_count: int, config: LlmRepresentationConfig) -> int:
    return max(8, math.ceil(word_count * config.summary_word_ratio))


def _representations_for_block(
    task: dict[str, Any] | None,
    generated: dict[str, Any],
    config: LlmRepresentationConfig,
) -> list[PdfBlockRepresentation]:
    if not task or not task["tasks"]:
        return []

    representations: list[PdfBlockRepresentation] = []
    if "keywords" in task["tasks"]:
        keywords = _normalize_keywords(generated.get("keywords"), limit=config.max_keywords)
        if keywords:
            representations.append(PdfBlockRepresentation(kind="keywords", label="Keywords", items=keywords))

    if "summary" in task["tasks"]:
        summary = _normalize_summary(generated.get("summary"), target_words=task["summary_target_words"])
        if summary:
            representations.append(PdfBlockRepresentation(kind="summary", label="Summary", text=summary))

    return representations


def _call_openai_batch(
    *,
    api_key: str,
    config: LlmRepresentationConfig,
    tasks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    response_payload = _post_openai_response(
        api_key=api_key,
        config=config,
        body={
            "model": config.model,
            "instructions": (
                "Generate concise reading aids for PDF paragraph blocks. "
                "Use only the supplied block text. Return JSON matching the schema. "
                "Honor each block's tasks list: generate keywords only when it includes keywords, "
                "and generate a summary only when it includes summary. "
                "Keywords must be specific noun phrases from the paragraph, not generic labels, UI placeholders, "
                "or process terms such as keyword, summary, paragraph, block, semantic parsing, or layout analysis "
                "unless those terms are central to the paragraph itself. "
                "Summaries must state the paragraph's main claim or finding and stay near each block's target word count. "
                "For unrequested fields, return an empty array or empty string."
            ),
            "input": json.dumps({"blocks": tasks}, ensure_ascii=False),
            "max_output_tokens": _estimate_max_output_tokens(tasks),
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pdf_block_representations",
                    "strict": True,
                    "schema": _response_schema(),
                }
            },
        },
    )
    output_text = _extract_output_text(response_payload)
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("OpenAI returned non-JSON representation output.") from error

    results: dict[str, dict[str, Any]] = {}
    for item in parsed.get("blocks", []):
        block_id = str(item.get("block_id", "")).strip()
        if block_id:
            results[block_id] = item
    return results


def _call_openai_representation(
    *,
    api_key: str,
    config: LlmRepresentationConfig,
    task: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Generate one compact representation for one paragraph block."""

    if kind not in {"keywords", "summary"}:
        raise ValueError(f"Unsupported representation kind: {kind}")

    budgets = _single_output_token_budgets(task=task, kind=kind)
    for index, max_output_tokens in enumerate(budgets):
        try:
            response_payload = _post_openai_response(
                api_key=api_key,
                config=config,
                body={
                    "model": config.model,
                    "instructions": _single_instructions(task=task, kind=kind),
                    "input": str(task.get("text") or ""),
                    "max_output_tokens": max_output_tokens,
                    "store": False,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": f"pdf_{kind}",
                            "strict": True,
                            "schema": _single_response_schema(kind),
                        }
                    },
                },
            )
            return _parse_single_representation_payload(response_payload, kind=kind)
        except ValueError as error:
            if index == len(budgets) - 1 or not _is_max_output_token_error(error):
                raise

    raise ValueError(f"OpenAI did not generate {kind}.")


def _single_instructions(*, task: dict[str, Any], kind: str) -> str:
    if kind == "keywords":
        return (
            f"Return JSON only. Extract up to {int(task.get('max_keywords') or 5)} specific noun phrases "
            "from the text. Use the text's own terminology. Avoid generic UI/process labels."
        )

    return (
        f"Return JSON only. Write one summary of about {int(task.get('summary_target_words') or 12)} words. "
        "State the paragraph's main claim or finding. Do not add outside facts."
    )


def _parse_single_representation_payload(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
    output_text = _extract_output_text(payload)
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("OpenAI returned non-JSON representation output.") from error

    if kind == "keywords":
        return {"keywords": parsed.get("k", [])}
    return {"summary": parsed.get("s", "")}


def _post_openai_response(
    *,
    api_key: str,
    config: LlmRepresentationConfig,
    body: dict[str, Any],
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=config.timeout_seconds) as client:
            response = client.post(config.endpoint, headers=headers, json=body)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise ValueError(f"OpenAI representation generation failed: {error}") from error
    return response.json()


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    if payload.get("error"):
        raise ValueError(f"OpenAI response error: {_compact_json(payload['error'])}")

    if payload.get("status") == "incomplete":
        raise ValueError(f"OpenAI response was incomplete: {_compact_json(payload.get('incomplete_details') or {})}")

    for item in _as_list(payload.get("output")):
        text = _extract_text_from_output_item(item)
        if text:
            return text

    for choice in _as_list(payload.get("choices")):
        text = _extract_text_from_choice(choice)
        if text:
            return text

    output_shape = _describe_output_shape(payload)
    raise ValueError(f"OpenAI response did not include output text. Response shape: {output_shape}")


def _extract_text_from_output_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""

    if item.get("type") == "output_text" and isinstance(item.get("text"), str) and item["text"].strip():
        return item["text"]

    if item.get("type") == "refusal":
        raise ValueError(f"OpenAI refused the request: {item.get('refusal') or item.get('text') or 'refusal'}")

    for content in _as_list(item.get("content")):
        if not isinstance(content, dict):
            if isinstance(content, str) and content.strip():
                return content
            continue

        content_type = content.get("type")
        if content_type == "refusal":
            raise ValueError(f"OpenAI refused the request: {content.get('refusal') or content.get('text') or 'refusal'}")
        if isinstance(content.get("text"), str) and content["text"].strip():
            return content["text"]
        if content.get("json") is not None:
            return json.dumps(content["json"], ensure_ascii=False)

    if isinstance(item.get("text"), str) and item["text"].strip():
        return item["text"]
    return ""


def _extract_text_from_choice(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""

    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        for item in _as_list(content):
            text = _extract_text_from_output_item(item)
            if text:
                return text

    text = choice.get("text")
    return text if isinstance(text, str) and text.strip() else ""


def _describe_output_shape(payload: dict[str, Any]) -> str:
    output_items = []
    for item in _as_list(payload.get("output")):
        if isinstance(item, dict):
            output_items.append(
                {
                    "type": item.get("type"),
                    "content_types": [
                        content.get("type")
                        for content in _as_list(item.get("content"))
                        if isinstance(content, dict)
                    ],
                }
            )
        else:
            output_items.append(type(item).__name__)
    return _compact_json({"status": payload.get("status"), "output": output_items})


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:600]


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "summary": {"type": "string"},
                    },
                    "required": ["block_id", "keywords", "summary"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["blocks"],
        "additionalProperties": False,
    }


def _single_response_schema(kind: str) -> dict[str, Any]:
    if kind == "keywords":
        return {
            "type": "object",
            "properties": {
                "k": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["k"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
        },
        "required": ["s"],
        "additionalProperties": False,
    }


def _estimate_max_output_tokens(tasks: list[dict[str, Any]]) -> int:
    summary_words = sum(int(task.get("summary_target_words") or 0) for task in tasks)
    keyword_tokens = sum(int(task.get("max_keywords") or 0) * 4 for task in tasks)
    return max(2048, min(8192, (summary_words + keyword_tokens + len(tasks) * 16) * 4))


def _estimate_single_max_output_tokens(task: dict[str, Any], kind: str) -> int:
    if kind == "keywords":
        return 2048

    target_words = int(task.get("summary_target_words") or 12)
    return max(2048, min(4096, (target_words + 32) * 10))


def _single_output_token_budgets(*, task: dict[str, Any], kind: str) -> list[int]:
    initial_budget = _estimate_single_max_output_tokens(task, kind)
    if initial_budget >= 8192:
        return [initial_budget]
    return [initial_budget, 8192]


def _is_max_output_token_error(error: ValueError) -> bool:
    return "max_output_tokens" in str(error)


def _normalize_keywords(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []

    keywords: list[str] = []
    seen: set[str] = set()
    for item in value:
        keyword = " ".join(str(item).split()).strip(" .,:;")
        key = keyword.lower()
        if not keyword or key in seen:
            continue
        keywords.append(keyword)
        seen.add(key)
        if len(keywords) >= limit:
            break
    return keywords


def _normalize_summary(value: Any, target_words: int) -> str:
    summary = " ".join(str(value or "").split())
    if not summary:
        return ""

    max_words = max(target_words + 8, math.ceil(target_words * 1.35))
    words = summary.split()
    if len(words) <= max_words:
        return summary
    return " ".join(words[:max_words]).rstrip(" ,;:") + "..."


def _batched(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), max(size, 1)):
        yield items[index : index + size]


def _positive_int(value: int | None, default: int) -> int:
    if value is None:
        return default
    return max(1, int(value))


def _bounded_float(value: float | None, default: float, *, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    return min(max(float(value), minimum), maximum)


def _bounded_int(value: int | str | None, default: int, *, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)

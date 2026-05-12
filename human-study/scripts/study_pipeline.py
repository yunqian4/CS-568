"""Shared helpers for the human-study preparation pipeline."""

from __future__ import annotations

import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKSPACE.parent
PDF_DIR = WORKSPACE / "pdfs"
DOCUMENT_DIR = WORKSPACE / "documents"
EXAM_DIR = WORKSPACE / "exams"
PRIVATE_R2_DIR = WORKSPACE / "private-r2"
DEFAULT_CONFIG = WORKSPACE / "config" / "default_document_config.json"
SCHEDULER_PATH = WORKSPACE / "scheduler.json"
PUBLIC_CACHE = REPO_ROOT / "frontend" / "public" / "study-cache"
MAX_PAGES_ASSET_BYTES = 25 * 1024 * 1024

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import app as backend_app  # noqa: E402
from backend.core.representations.llm import LlmRepresentationConfig, RepresentationDefinition  # noqa: E402
from backend.core.representations.jobs import (  # noqa: E402
    initialize_representation_jobs,
    merge_completed_representations,
    run_representation_jobs,
)


ENV_CONFIG_KEYS = {
    "openai_representation_parallelism": "OPENAI_REPRESENTATION_PARALLELISM",
    "openai_request_retries": "OPENAI_REQUEST_RETRIES",
    "openai_request_timeout_seconds": "OPENAI_REQUEST_TIMEOUT_SECONDS",
}


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a stable, editable JSON object."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_document_config(document_id: str) -> dict[str, Any]:
    """Load defaults plus optional per-document overrides."""

    config = load_json(DEFAULT_CONFIG)
    override_path = PDF_DIR / document_id / "config.json"
    if override_path.exists():
        config = deep_merge(config, load_json(override_path))
    editable_path = DOCUMENT_DIR / document_id / "config.json"
    if editable_path.exists():
        config = deep_merge(config, load_json(editable_path))
    return config


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a recursive merge where override values win."""

    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def document_ids(selected: str | None = None) -> list[str]:
    """Return selected or discovered document ids."""

    if selected:
        return [selected]
    return sorted(path.name for path in PDF_DIR.iterdir() if path.is_dir() and source_pdf_for(path.name).exists())


def source_pdf_for(document_id: str) -> Path:
    """Return the expected source PDF path for a study document."""

    direct = PDF_DIR / document_id / "source.pdf"
    if direct.exists():
        return direct
    candidates = sorted((PDF_DIR / document_id).glob("*.pdf"))
    if candidates:
        return candidates[0]
    return direct


def document_workspace(document_id: str) -> Path:
    """Return the editable workspace for one document."""

    return DOCUMENT_DIR / document_id


def apply_env_config(config: dict[str, Any]) -> None:
    """Apply request-level OpenAI tuning as process env for existing helpers."""

    llm = nested_llm_config(config)
    for json_key, env_key in ENV_CONFIG_KEYS.items():
        value = llm.get(json_key, config.get(json_key))
        if value is not None:
            os.environ[env_key] = str(value)


def nested_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return the nested LLM config object."""

    value = config.get("llm") or config.get("llm_options")
    return value if isinstance(value, dict) else {}


def representation_config(config: dict[str, Any], *, enabled: bool | None = None) -> LlmRepresentationConfig:
    """Build the backend representation config from editable JSON."""

    llm = nested_llm_config(config)
    active_enabled = llm.get("enabled", config.get("llm_enabled", True)) if enabled is None else enabled
    return LlmRepresentationConfig.from_values(
        enabled=bool(active_enabled),
        api_key=first_string(llm, config, "api_key", "openai_api_key", "llm_api_key"),
        model=first_string(llm, config, "model", "openai_model", "llm_model"),
        keyword_min_words=first_int(llm, config, "keyword_min_words"),
        summary_min_words=first_int(llm, config, "summary_min_words"),
        summary_word_ratio=first_float(llm, config, "summary_word_ratio"),
        max_keywords=first_int(llm, config, "max_keywords"),
        representations=representation_definitions(config),
    )


def semantic_config(config: dict[str, Any]) -> LlmRepresentationConfig:
    """Build a config that enables semantic grouping but creates no representation jobs."""

    semantic = config.get("semantic") if isinstance(config.get("semantic"), dict) else {}
    semantic_enabled = bool(semantic.get("enabled", True))
    llm = representation_config(config, enabled=semantic_enabled)
    llm.representations = [
        RepresentationDefinition(name="disabled", prompt="disabled", enabled=False)
    ]
    return llm


def representation_definitions(config: dict[str, Any]) -> list[RepresentationDefinition] | None:
    """Parse editable representation definitions."""

    llm = nested_llm_config(config)
    raw = llm.get("representations", config.get("representations"))
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("llm.representations must be a list.")
    return [
        RepresentationDefinition(
            name=str(item.get("name") or ""),
            prompt=str(item.get("prompt") or ""),
            background_color=str(item.get("background_color") or "#263238"),
            background_opacity=safe_float(item.get("background_opacity"), 1.0),
            enabled=bool(item.get("enabled", True)),
        ).normalized()
        for item in raw
        if isinstance(item, dict)
    ]


def require_llm_key(config: LlmRepresentationConfig, label: str) -> None:
    """Fail early when a configured LLM stage lacks credentials."""

    if config.enabled and not (config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip()):
        raise ValueError(f"{label} requires llm.api_key or OPENAI_API_KEY.")


def write_editable_intermediates(document_id: str, payload: dict[str, Any], config: dict[str, Any], source_pdf: Path) -> None:
    """Write editable document, chunk, paragraph, and config files."""

    workspace = document_workspace(document_id)
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_pdf, workspace / "source.pdf")
    write_json(workspace / "config.json", config)
    write_json(workspace / "document.json", payload)
    write_json(workspace / "chunks.json", {"pages": payload.get("pages", [])})
    write_json(
        workspace / "paragraphs.json",
        {
            "blocks": [
                {
                    "block_id": block.get("block_id"),
                    "chunk_ids": block.get("chunk_ids", []),
                    "page_number": block.get("page_number"),
                    "section_path": block.get("section_path", []),
                    "text": block.get("text", ""),
                }
                for block in payload.get("blocks", [])
                if isinstance(block, dict)
            ]
        },
    )


def load_editable_document(document_id: str) -> tuple[dict[str, Any], Path]:
    """Load document.json and apply optional chunk/paragraph edits."""

    workspace = document_workspace(document_id)
    document_path = workspace / "document.json"
    if not document_path.exists():
        raise FileNotFoundError(f"Missing {document_path}. Run 1_extract_document.py first.")
    payload = load_json(document_path)

    chunks_path = workspace / "chunks.json"
    if chunks_path.exists():
        chunks = load_json(chunks_path)
        if isinstance(chunks.get("pages"), list):
            payload["pages"] = chunks["pages"]

    paragraphs_path = workspace / "paragraphs.json"
    if paragraphs_path.exists():
        paragraphs = load_json(paragraphs_path)
        blocks = paragraphs.get("blocks")
        if isinstance(blocks, list):
            existing = {
                str(block.get("block_id")): block
                for block in payload.get("blocks", [])
                if isinstance(block, dict) and block.get("block_id")
            }
            next_blocks = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_id = str(block.get("block_id") or "")
                merged = {**existing.get(block_id, {}), **block}
                merged.setdefault("representations", [])
                next_blocks.append(merged)
            payload["blocks"] = next_blocks

    return payload, document_path


def persist_document_payload(document_id: str, payload: dict[str, Any]) -> None:
    """Persist canonical editable document JSON."""

    workspace = document_workspace(document_id)
    write_json(workspace / "document.json", payload)
    write_json(workspace / "chunks.json", {"pages": payload.get("pages", [])})
    write_json(
        workspace / "paragraphs.json",
        {
            "blocks": [
                {
                    "block_id": block.get("block_id"),
                    "chunk_ids": block.get("chunk_ids", []),
                    "page_number": block.get("page_number"),
                    "representations": block.get("representations", []),
                    "section_path": block.get("section_path", []),
                    "text": block.get("text", ""),
                }
                for block in payload.get("blocks", [])
                if isinstance(block, dict)
            ]
        },
    )


def run_representation_generation(document_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Generate representation files and merge them into editable document JSON."""

    apply_env_config(config)
    llm_config = representation_config(config)
    require_llm_key(llm_config, "Representation generation")
    payload, _ = load_editable_document(document_id)
    provider_dir = document_workspace(document_id) / "providers" / "opendataloader"
    cached_document = backend_app._document_from_cached_payload(payload)
    status = initialize_representation_jobs(cached_document, llm_config, provider_dir)
    payload.setdefault("metadata", {})["llm_representations"] = status
    payload["metadata"]["representation_definitions"] = [
        definition.to_dict() for definition in llm_config.representations
    ]
    run_representation_jobs(cached_document, llm_config, provider_dir)
    payload = merge_completed_representations(payload, provider_dir)
    persist_document_payload(document_id, payload)
    return payload


def first_string(primary: dict[str, Any], fallback: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string across primary and fallback configs."""

    for key in keys:
        value = primary.get(key, fallback.get(key))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def first_int(primary: dict[str, Any], fallback: dict[str, Any], key: str) -> int | None:
    """Return an optional integer config value."""

    value = primary.get(key, fallback.get(key))
    return None if value is None else int(value)


def first_float(primary: dict[str, Any], fallback: dict[str, Any], key: str) -> float | None:
    """Return an optional float config value."""

    value = primary.get(key, fallback.get(key))
    return None if value is None else float(value)


def safe_float(value: Any, fallback: float) -> float:
    """Parse a float with a fallback."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def reset_dir(path: Path) -> None:
    """Replace a directory."""

    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

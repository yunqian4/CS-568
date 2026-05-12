"""Prepare one cached PDF document and its generated representations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from backend import app as backend_app
from backend.core.representations.llm import LlmRepresentationConfig, RepresentationDefinition


ENV_CONFIG_KEYS = {
    "openai_representation_parallelism": "OPENAI_REPRESENTATION_PARALLELISM",
    "openai_request_retries": "OPENAI_REQUEST_RETRIES",
    "openai_request_timeout_seconds": "OPENAI_REQUEST_TIMEOUT_SECONDS",
}


def main() -> None:
    """Run the command-line preparation workflow."""

    args = _parse_args()
    pdf_path = Path(args.pdf).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else None

    payload = prepare_document(pdf_path=pdf_path, config_path=config_path)
    result = _result_summary(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def prepare_document(*, pdf_path: Path, config_path: Path) -> dict[str, Any]:
    """Prepare cached artifacts for one PDF using a JSON config file."""

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Config JSON not found: {config_path}")

    config = _read_json(config_path)
    _apply_environment_config(config)
    llm_config = _llm_config_from_json(config)
    if llm_config.enabled and not _has_llm_key(llm_config):
        raise ValueError("LLM preparation requires llm.api_key or OPENAI_API_KEY.")

    provider = str(config.get("provider") or config.get("parser") or "opendataloader").strip() or "opendataloader"
    source_name = str(config.get("source_name") or pdf_path.name)
    return backend_app._store_and_parse_pdf(
        source_name=source_name,
        pdf_bytes=pdf_path.read_bytes(),
        provider=provider,
        llm_config=llm_config,
        background_tasks=None,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a PDF and synchronously generate cached LLM semantic grouping "
            "and block representations for human-evaluation deployment."
        )
    )
    parser.add_argument("--pdf", required=True, help="Path to the source PDF.")
    parser.add_argument("--config", required=True, help="Path to the JSON preparation config.")
    parser.add_argument("--output", help="Optional path for a small JSON result summary.")
    return parser.parse_args()


def _llm_config_from_json(config: dict[str, Any]) -> LlmRepresentationConfig:
    llm = _nested_config(config)
    enabled = llm.get("enabled")
    if enabled is None:
        enabled = config.get("llm_enabled", True)

    return LlmRepresentationConfig.from_values(
        enabled=bool(enabled),
        api_key=_first_string(llm, config, "api_key", "openai_api_key", "llm_api_key"),
        model=_first_string(llm, config, "model", "openai_model", "llm_model"),
        keyword_min_words=_first_int(llm, config, "keyword_min_words"),
        summary_min_words=_first_int(llm, config, "summary_min_words"),
        summary_word_ratio=_first_float(llm, config, "summary_word_ratio"),
        max_keywords=_first_int(llm, config, "max_keywords"),
        representations=_representation_definitions(config, llm),
    )


def _nested_config(config: dict[str, Any]) -> dict[str, Any]:
    for key in ("llm", "llm_options"):
        value = config.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _representation_definitions(config: dict[str, Any], llm: dict[str, Any]) -> list[RepresentationDefinition] | None:
    raw_definitions = llm.get("representations", config.get("representations"))
    if raw_definitions is None:
        return None
    if not isinstance(raw_definitions, list):
        raise ValueError("representations must be a list.")

    definitions: list[RepresentationDefinition] = []
    for index, item in enumerate(raw_definitions):
        if not isinstance(item, dict):
            raise ValueError(f"representations[{index}] must be an object.")
        definitions.append(
            RepresentationDefinition(
                name=str(item.get("name") or ""),
                prompt=str(item.get("prompt") or ""),
                background_color=str(item.get("background_color") or "#263238"),
                background_opacity=_safe_float(item.get("background_opacity"), 1.0),
                enabled=bool(item.get("enabled", True)),
            ).normalized()
        )
    return definitions


def _apply_environment_config(config: dict[str, Any]) -> None:
    llm = _nested_config(config)
    for json_key, env_key in ENV_CONFIG_KEYS.items():
        value = llm.get(json_key, config.get(json_key))
        if value is not None:
            os.environ[env_key] = str(value)


def _has_llm_key(config: LlmRepresentationConfig) -> bool:
    return bool(config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip())


def _result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    status = metadata.get("llm_representations") if isinstance(metadata.get("llm_representations"), dict) else {}
    document_id = str(payload.get("document_id") or payload.get("content_hash") or "")
    provider = str(payload.get("provider") or metadata.get("provider") or "opendataloader")
    return {
        "document_id": document_id,
        "document_json": f"dat/temp/{document_id}/providers/{provider}/document.json",
        "page_count": payload.get("page_count"),
        "provider": provider,
        "representation_status": {
            "completed_jobs": status.get("completed_jobs"),
            "failed_jobs": status.get("failed_jobs"),
            "status": status.get("status"),
            "total_jobs": status.get("total_jobs"),
        },
        "source_pdf": f"dat/temp/{document_id}/source.pdf",
        "title": payload.get("title"),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON config: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError("Config JSON must be an object.")
    return payload


def _first_int(primary: dict[str, Any], fallback: dict[str, Any], key: str) -> int | None:
    value = primary.get(key, fallback.get(key))
    if value is None:
        return None
    return int(value)


def _first_float(primary: dict[str, Any], fallback: dict[str, Any], key: str) -> float | None:
    value = primary.get(key, fallback.get(key))
    if value is None:
        return None
    return float(value)


def _first_string(primary: dict[str, Any], fallback: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = primary.get(key, fallback.get(key))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


if __name__ == "__main__":
    main()

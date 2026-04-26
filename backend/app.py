"""FastAPI application for the PDF reader prototype."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent
TEMP_DOCUMENT_ROOT = REPO_ROOT / "dat" / "temp"
SOURCE_PDF_NAME = "source.pdf"
MANIFEST_NAME = "manifest.json"
DOCUMENT_JSON_NAME = "document.json"
CONTENT_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
CACHE_VERSION = "opendataloader-llm-progressive-v8"

from .core.ingest import parse_pdf_provider_output
from .core.ingest.builders import build_pdf_document_from_parsed_pdf
from .core.ingest.llm_semantic import LlmSemanticConfig, apply_llm_semantic_grouping
from .core.models import PdfBlock, PdfDocument
from .core.representations import LlmRepresentationConfig
from .core.representations.jobs import (
    initialize_representation_jobs,
    merge_completed_representations,
    representation_snapshot,
    reset_failed_representation_jobs,
    run_representation_jobs,
)


def _load_default_env_files() -> None:
    """Load local env defaults without overriding the process environment."""

    for env_path in (BACKEND_ROOT / ".env", REPO_ROOT / ".env"):
        _load_env_file(env_path)


def _load_env_file(env_path: Path) -> None:
    """Load simple KEY=VALUE pairs from a dotenv file."""

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        cleaned_value = _clean_env_value(value)
        if cleaned_value == "":
            continue
        os.environ[key] = cleaned_value


def _clean_env_value(value: str) -> str:
    """Normalize a dotenv value after KEY= splitting."""

    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1]
    return cleaned


_load_default_env_files()

app = FastAPI(title="PDF Reader API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LlmOptionsRequest(BaseModel):
    """Request payload for LLM-generated block representations."""

    enabled: bool | None = None
    api_key: str | None = None
    model: str | None = None
    keyword_min_words: int | None = None
    summary_min_words: int | None = None
    summary_word_ratio: float | None = None
    max_keywords: int | None = None

    def to_config(self) -> LlmRepresentationConfig:
        """Convert request values to backend representation settings."""

        return LlmRepresentationConfig.from_values(
            enabled=self.enabled,
            api_key=self.api_key,
            model=self.model,
            keyword_min_words=self.keyword_min_words,
            summary_min_words=self.summary_min_words,
            summary_word_ratio=self.summary_word_ratio,
            max_keywords=self.max_keywords,
        )


class PdfUrlRequest(BaseModel):
    """Request payload for importing a remote PDF."""

    url: HttpUrl
    provider: str = "native"
    llm_options: LlmOptionsRequest | None = None


@app.get("/api/health")
def healthcheck() -> dict[str, str]:
    """Simple probe used by the frontend during development."""

    return {"status": "ok"}


@app.get("/api/llm/config")
def get_llm_config() -> dict[str, object]:
    """Return non-secret LLM defaults for the frontend."""

    return {
        "has_default_key": bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
        "default_model": LlmRepresentationConfig.from_values().model,
    }


@app.post("/api/documents/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    provider: str = "native",
    llm_enabled: bool | None = Form(None),
    llm_api_key: str | None = Form(None),
    llm_model: str | None = Form(None),
    keyword_min_words: int | None = Form(None),
    summary_min_words: int | None = Form(None),
    summary_word_ratio: float | None = Form(None),
    max_keywords: int | None = Form(None),
) -> dict[str, object]:
    """Store an uploaded PDF and return its parsed structure."""

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    pdf_bytes = await file.read()
    llm_config = LlmRepresentationConfig.from_values(
        enabled=_llm_enabled_for_provider(provider, llm_enabled),
        api_key=llm_api_key,
        model=llm_model,
        keyword_min_words=keyword_min_words,
        summary_min_words=summary_min_words,
        summary_word_ratio=summary_word_ratio,
        max_keywords=max_keywords,
    )
    return _store_and_parse_pdf(
        source_name=file.filename,
        pdf_bytes=pdf_bytes,
        provider=provider,
        llm_config=llm_config,
        background_tasks=background_tasks,
    )


@app.post("/api/documents/from-url")
async def import_pdf_from_url(payload: PdfUrlRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    """Fetch a PDF from a URL and return its parsed structure."""

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(str(payload.url))
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=400, detail=f"Unable to fetch PDF: {error}") from error

    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not str(payload.url).lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="The provided URL does not appear to be a PDF.")

    source_name = Path(urlparse(str(payload.url)).path).name or "remote.pdf"
    return _store_and_parse_pdf(
        source_name=source_name,
        pdf_bytes=response.content,
        provider=payload.provider,
        llm_config=_llm_config_from_url_payload(payload),
        background_tasks=background_tasks,
    )


@app.get("/api/documents/{document_id}/representations")
def get_document_representations(
    document_id: str,
    provider: str = Query("opendataloader"),
) -> dict[str, object]:
    """Return cached representation results for a document."""

    provider_dir = _document_dir_for_id(document_id) / "providers" / _safe_artifact_name(provider)
    if not provider_dir.exists():
        raise HTTPException(status_code=404, detail="Document provider artifacts not found.")
    return representation_snapshot(provider_dir)


@app.get("/api/documents/{document_id}/file")
def get_pdf_file(document_id: str) -> FileResponse:
    """Serve the stored PDF back to the frontend viewer."""

    document_dir = _document_dir_for_id(document_id)
    pdf_path = document_dir / SOURCE_PDF_NAME
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Document not found.")
    source_name = _read_manifest(document_dir).get("source_name") or pdf_path.name
    return FileResponse(pdf_path, media_type="application/pdf", filename=str(source_name))


def _store_and_parse_pdf(
    source_name: str,
    pdf_bytes: bytes,
    provider: str = "native",
    llm_config: LlmRepresentationConfig | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, object]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")

    document_id = hashlib.sha256(pdf_bytes).hexdigest()
    document_dir = _document_dir_for_id(document_id)
    document_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = document_dir / SOURCE_PDF_NAME
    pdf_path.write_bytes(pdf_bytes)
    provider_key = _safe_artifact_name(provider.lower().strip() or "native")
    provider_dir = document_dir / "providers" / provider_key
    provider_dir.mkdir(parents=True, exist_ok=True)
    active_llm_config = llm_config or LlmRepresentationConfig(enabled=False)
    cache_profile = _cache_profile(provider=provider_key, llm_config=active_llm_config)

    cached_payload = _read_cached_document_payload(
        document_dir=document_dir,
        provider_dir=provider_dir,
        cache_profile=cache_profile,
    )
    if cached_payload is not None:
        cached_payload = _resume_cached_representation_jobs(
            cached_payload=cached_payload,
            llm_config=active_llm_config,
            provider_dir=provider_dir,
            background_tasks=background_tasks,
        )
        return cached_payload

    try:
        parsed_document = parse_pdf_provider_output(source_name=source_name, pdf_bytes=pdf_bytes, provider=provider)
        semantic_metadata = _apply_provider_semantic_enrichment(
            parsed_document=parsed_document,
            provider=provider_key,
            llm_config=active_llm_config,
            provider_dir=provider_dir,
        )
        document = build_pdf_document_from_parsed_pdf(
            document_id=document_id,
            source_name=source_name,
            provider=provider_key,
            parsed_document=parsed_document,
        )
    except ValueError as error:
        _write_manifest(
            document_dir=document_dir,
            source_name=source_name,
            document_id=document_id,
            provider=provider_key,
            provider_status={"status": "failed", "error": str(error)},
        )
        raise HTTPException(status_code=400, detail=str(error)) from error

    if semantic_metadata.get("enabled"):
        document.metadata["llm_semantic"] = semantic_metadata
    document.metadata["cache_version"] = CACHE_VERSION
    document.metadata["cache_profile"] = cache_profile

    if active_llm_config.enabled:
        _clear_block_representations(document)
    representation_status = initialize_representation_jobs(document, active_llm_config, provider_dir)
    document.metadata["llm_representations"] = representation_status

    payload = document.to_dict()
    payload["pdf_url"] = f"/api/documents/{document_id}/file"
    payload["provider"] = document.metadata.get("provider", provider)
    payload["content_hash"] = document_id

    actual_provider = _safe_artifact_name(str(payload["provider"]))
    provider_dir = document_dir / "providers" / actual_provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    document_json_path = provider_dir / DOCUMENT_JSON_NAME
    _write_json(document_json_path, payload)
    _write_manifest(
        document_dir=document_dir,
        source_name=source_name,
        document_id=document_id,
        provider=actual_provider,
        provider_status={
            "status": "parsed",
            "document_json": _relative_path(document_json_path, document_dir),
            "parser": document.metadata.get("parser"),
            "paragraph_count": document.metadata.get("paragraph_count"),
            "llm_representations": document.metadata.get("llm_representations"),
            "cache_version": CACHE_VERSION,
            "cache_profile": cache_profile,
        },
    )

    if active_llm_config.enabled:
        if background_tasks is not None:
            background_tasks.add_task(run_representation_jobs, document, active_llm_config, provider_dir)
        else:
            run_representation_jobs(document, active_llm_config, provider_dir)
            payload = merge_completed_representations(payload, provider_dir)
            _write_json(document_json_path, payload)
    return payload


def _resume_cached_representation_jobs(
    *,
    cached_payload: dict[str, object],
    llm_config: LlmRepresentationConfig,
    provider_dir: Path,
    background_tasks: BackgroundTasks | None,
) -> dict[str, object]:
    """Restart unfinished cached representation jobs without reparsing the PDF."""

    if not llm_config.enabled:
        return cached_payload

    cached_document = _document_from_cached_payload(cached_payload)
    if _has_representation_api_key(llm_config):
        retry_status = reset_failed_representation_jobs(provider_dir)
        cached_payload.setdefault("metadata", {})["llm_representations"] = retry_status

    if background_tasks is not None:
        background_tasks.add_task(run_representation_jobs, cached_document, llm_config, provider_dir, True)
        return cached_payload

    run_representation_jobs(cached_document, llm_config, provider_dir, retry_failed=True)
    refreshed_payload = merge_completed_representations(cached_payload, provider_dir)
    _write_json(provider_dir / DOCUMENT_JSON_NAME, refreshed_payload)
    return refreshed_payload


def _document_from_cached_payload(payload: dict[str, object]) -> PdfDocument:
    """Rebuild the block subset needed by background representation jobs."""

    blocks: list[PdfBlock] = []
    for item in _safe_list(payload.get("blocks")):
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id") or "").strip()
        if not block_id:
            continue
        blocks.append(
            PdfBlock(
                block_id=block_id,
                page_number=_safe_int(item.get("page_number"), 1),
                text=str(item.get("text") or ""),
                chunk_ids=[str(chunk_id) for chunk_id in _safe_list(item.get("chunk_ids"))],
                section_path=[str(part) for part in _safe_list(item.get("section_path"))],
                representations=[],
            )
        )

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return PdfDocument(
        document_id=str(payload.get("document_id") or ""),
        title=str(payload.get("title") or ""),
        source_name=str(payload.get("source_name") or ""),
        page_count=_safe_int(payload.get("page_count"), 0),
        blocks=blocks,
        metadata=dict(metadata),
    )


def _clear_block_representations(document: PdfDocument) -> None:
    """Remove heuristic placeholders before progressive LLM output arrives."""

    for block in document.blocks:
        block.representations = []


def _has_representation_api_key(llm_config: LlmRepresentationConfig) -> bool:
    return bool(llm_config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip())


def _read_cached_document_payload(
    *,
    document_dir: Path,
    provider_dir: Path,
    cache_profile: str,
) -> dict[str, object] | None:
    document_json_path = provider_dir / DOCUMENT_JSON_NAME
    if not document_json_path.exists():
        return None

    try:
        payload = json.loads(document_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if metadata.get("cache_version") != CACHE_VERSION or metadata.get("cache_profile") != cache_profile:
        return None

    document_id = str(payload.get("document_id") or document_dir.name)
    payload["pdf_url"] = f"/api/documents/{document_id}/file"
    payload["content_hash"] = document_id
    payload["provider"] = metadata.get("provider") or provider_dir.name
    return merge_completed_representations(payload, provider_dir)


def _cache_profile(provider: str, llm_config: LlmRepresentationConfig) -> str:
    semantic_mode = "opendataloader-llm" if provider == "opendataloader" and llm_config.enabled else "heuristic"
    representation_mode = "llm" if llm_config.enabled else "placeholder"
    return "|".join(
        [
            CACHE_VERSION,
            f"provider={provider}",
            f"semantic={semantic_mode}",
            f"representations={representation_mode}",
            f"model={llm_config.model}",
            f"kw_min={llm_config.keyword_min_words}",
            f"summary_min={llm_config.summary_min_words}",
            f"summary_ratio={llm_config.summary_word_ratio}",
            f"max_keywords={llm_config.max_keywords}",
        ]
    )


def _apply_provider_semantic_enrichment(
    *,
    parsed_document,
    provider: str,
    llm_config: LlmRepresentationConfig,
    provider_dir: Path,
) -> dict[str, object]:
    if provider != "opendataloader" or not llm_config.enabled:
        return {"enabled": False}

    semantic_config = LlmSemanticConfig.from_values(
        enabled=True,
        api_key=llm_config.api_key,
        model=llm_config.model,
        artifact_dir=provider_dir,
    )
    return apply_llm_semantic_grouping(parsed_document, semantic_config)


def _llm_enabled_for_provider(provider: str, value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    return provider.lower().strip() == "opendataloader"


def _llm_config_from_url_payload(payload: PdfUrlRequest) -> LlmRepresentationConfig:
    if payload.llm_options:
        options = payload.llm_options.to_config()
        if payload.llm_options.enabled is None:
            options.enabled = _llm_enabled_for_provider(payload.provider, None)
        return options
    return LlmRepresentationConfig.from_values(enabled=_llm_enabled_for_provider(payload.provider, None))


def _document_dir_for_id(document_id: str) -> Path:
    if not CONTENT_HASH_RE.fullmatch(document_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    return TEMP_DOCUMENT_ROOT / document_id


def _write_manifest(
    document_dir: Path,
    source_name: str,
    document_id: str,
    provider: str,
    provider_status: dict[str, object],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    manifest = _read_manifest(document_dir)
    manifest.setdefault("created_at", now)
    manifest.update(
        {
            "document_id": document_id,
            "content_sha256": document_id,
            "source_name": source_name,
            "source_pdf": SOURCE_PDF_NAME,
            "updated_at": now,
        }
    )
    providers = manifest.setdefault("providers", {})
    providers[provider] = {**provider_status, "updated_at": now}
    _write_json(document_dir / MANIFEST_NAME, manifest)


def _read_manifest(document_dir: Path) -> dict[str, object]:
    manifest_path = document_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_artifact_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return safe or "default"


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()

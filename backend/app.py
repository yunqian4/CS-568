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
CACHE_VERSION = "opendataloader-llm-progressive-v10"

from .core.ingest import parse_pdf_provider_output
from .core.ingest.builders import build_pdf_document_from_parsed_pdf
from .core.ingest.llm_semantic import LlmSemanticConfig, apply_llm_semantic_grouping
from .core.models import PdfBlock, PdfDocument
from .core.representations import LlmRepresentationConfig, build_default_block_representations
from .core.representations.llm import RepresentationDefinition, OPENAI_RESPONSES_URL, DEFAULT_MODEL, _extract_output_text
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
    representations: list["RepresentationDefinitionRequest"] | None = None

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
            representations=_request_representation_definitions(self.representations),
        )


class RepresentationDefinitionRequest(BaseModel):
    """User-editable prompt and color for one block representation."""

    name: str = ""
    prompt: str = ""
    background_color: str = "#263238"
    background_opacity: float = 1.0
    enabled: bool = True

    def to_definition(self) -> RepresentationDefinition:
        """Convert request values to representation settings."""

        return RepresentationDefinition(
            name=self.name,
            prompt=self.prompt,
            background_color=self.background_color,
            background_opacity=self.background_opacity,
            enabled=self.enabled,
        ).normalized()


class PdfUrlRequest(BaseModel):
    """Request payload for importing a remote PDF."""

    url: HttpUrl
    provider: str = "native"
    llm_options: LlmOptionsRequest | None = None


class RegenerateRepresentationsRequest(BaseModel):
    """Request payload for regenerating cached block representations."""

    provider: str = "opendataloader"
    llm_options: LlmOptionsRequest | None = None


class QuizRequest(BaseModel):
    """Request payload for generating quiz questions from a document."""

    provider: str = "opendataloader"
    api_key: str | None = None
    model: str | None = None


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


@app.post("/api/documents/{document_id}/quiz")
async def generate_quiz(document_id: str, payload: QuizRequest) -> dict[str, object]:
    """Generate 3 SAT-style multiple-choice questions from a document's text blocks."""

    document_dir = _document_dir_for_id(document_id)
    provider = _safe_artifact_name(payload.provider)
    document_json_path = document_dir / "providers" / provider / DOCUMENT_JSON_NAME

    if not document_json_path.exists():
        # Fall back to any available provider's document.json so the quiz
        # works even when the frontend sends the wrong provider name (e.g.
        # after a failed opendataloader upload followed by a native retry).
        providers_dir = document_dir / "providers"
        if providers_dir.exists():
            for sub in sorted(providers_dir.iterdir()):
                candidate = sub / DOCUMENT_JSON_NAME
                if sub.is_dir() and candidate.exists():
                    document_json_path = candidate
                    break
        if not document_json_path.exists():
            raise HTTPException(status_code=404, detail="Document not found.")

    doc_data = _read_json(document_json_path)
    blocks = doc_data.get("blocks") if isinstance(doc_data.get("blocks"), list) else []
    full_text = "\n\n".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("text"))

    if not full_text.strip():
        raise HTTPException(status_code=400, detail="No extractable text found in this document.")

    api_key = (payload.api_key or "").strip() or (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key available for quiz generation.")

    model = (payload.model or "").strip() or (os.environ.get("OPENAI_REPRESENTATION_MODEL") or DEFAULT_MODEL)

    try:
        questions = _generate_quiz_questions(text=full_text, api_key=api_key, model=model)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return {"questions": questions}


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
    representations: str | None = Form(None),
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
        representations=_parse_form_representation_definitions(representations),
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


@app.post("/api/documents/{document_id}/representations/regenerate")
def regenerate_document_representations(
    document_id: str,
    payload: RegenerateRepresentationsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """Restart representation jobs for an existing cached document."""

    document_dir = _document_dir_for_id(document_id)
    provider = _safe_artifact_name(payload.provider)
    provider_dir = document_dir / "providers" / provider
    document_json_path = provider_dir / DOCUMENT_JSON_NAME
    if not document_json_path.exists():
        raise HTTPException(status_code=404, detail="Document provider artifacts not found.")

    document_payload = _read_json(document_json_path)
    llm_config = payload.llm_options.to_config() if payload.llm_options else LlmRepresentationConfig.from_values(enabled=True)
    llm_config.enabled = True
    if not _has_representation_api_key(llm_config):
        raise HTTPException(status_code=400, detail="LLM representation regeneration requires a user API key or OPENAI_API_KEY.")

    cached_document = _document_from_cached_payload(document_payload)
    _clear_payload_representations(document_payload)
    status = initialize_representation_jobs(cached_document, llm_config, provider_dir)
    document_payload.setdefault("metadata", {})["llm_representations"] = status
    document_payload["metadata"]["representation_profile"] = _representation_profile(llm_config)
    document_payload["metadata"]["representation_definitions"] = [
        definition.to_dict() for definition in llm_config.representations
    ]
    document_payload["pdf_url"] = f"/api/documents/{document_id}/file"
    document_payload["provider"] = provider
    document_payload["content_hash"] = document_id
    _write_json(document_json_path, document_payload)
    background_tasks.add_task(run_representation_jobs, cached_document, llm_config, provider_dir)
    return document_payload


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
    representation_profile = _representation_profile(active_llm_config)

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
            cache_profile=cache_profile,
            representation_profile=representation_profile,
        )
        _write_manifest(
            document_dir=document_dir,
            source_name=source_name,
            document_id=document_id,
            provider=str(cached_payload.get("provider") or provider_key),
            provider_status=_provider_status_from_payload(cached_payload, cache_profile=cache_profile),
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
    document.metadata["representation_profile"] = representation_profile
    document.metadata["representation_definitions"] = [
        definition.to_dict() for definition in active_llm_config.representations
    ]

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
            "representation_profile": representation_profile,
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
    cache_profile: str,
    representation_profile: str,
) -> dict[str, object]:
    """Restart unfinished cached representation jobs without reparsing the PDF."""

    metadata = cached_payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        cached_payload["metadata"] = metadata

    stored_representation_profile = metadata.get("representation_profile")
    if not stored_representation_profile and _can_adopt_legacy_representation_profile(
        metadata=metadata,
        llm_config=llm_config,
        representation_profile=representation_profile,
    ):
        metadata["representation_profile"] = representation_profile
        metadata["representation_definitions"] = [
            definition.to_dict() for definition in llm_config.representations
        ]
        metadata["cache_profile"] = cache_profile
        _write_json(provider_dir / DOCUMENT_JSON_NAME, cached_payload)
    elif stored_representation_profile != representation_profile:
        return _restart_cached_representation_jobs(
            cached_payload=cached_payload,
            llm_config=llm_config,
            provider_dir=provider_dir,
            background_tasks=background_tasks,
            cache_profile=cache_profile,
            representation_profile=representation_profile,
        )

    metadata["cache_profile"] = cache_profile
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


def _restart_cached_representation_jobs(
    *,
    cached_payload: dict[str, object],
    llm_config: LlmRepresentationConfig,
    provider_dir: Path,
    background_tasks: BackgroundTasks | None,
    cache_profile: str,
    representation_profile: str,
) -> dict[str, object]:
    """Reset cached representation output after prompt or threshold changes."""

    cached_document = _document_from_cached_payload(cached_payload)
    metadata = cached_payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        cached_payload["metadata"] = metadata

    metadata["cache_profile"] = cache_profile
    metadata["representation_profile"] = representation_profile
    metadata["representation_definitions"] = [
        definition.to_dict() for definition in llm_config.representations
    ]

    if llm_config.enabled:
        _clear_payload_representations(cached_payload)
    else:
        _restore_payload_placeholder_representations(cached_payload)

    status = initialize_representation_jobs(cached_document, llm_config, provider_dir)
    metadata["llm_representations"] = status
    _write_json(provider_dir / DOCUMENT_JSON_NAME, cached_payload)

    if not llm_config.enabled:
        return cached_payload

    if background_tasks is not None:
        background_tasks.add_task(run_representation_jobs, cached_document, llm_config, provider_dir)
        return cached_payload

    run_representation_jobs(cached_document, llm_config, provider_dir)
    refreshed_payload = merge_completed_representations(cached_payload, provider_dir)
    _write_json(provider_dir / DOCUMENT_JSON_NAME, refreshed_payload)
    return refreshed_payload


def _can_adopt_legacy_representation_profile(
    *,
    metadata: dict[str, object],
    llm_config: LlmRepresentationConfig,
    representation_profile: str,
) -> bool:
    """Treat pre-profile default representation caches as current."""

    if not llm_config.enabled:
        return representation_profile == "placeholder"

    status = metadata.get("llm_representations") if isinstance(metadata.get("llm_representations"), dict) else {}
    if status.get("status") != "complete" or int(status.get("failed_jobs") or 0):
        return False

    legacy_default_config = LlmRepresentationConfig.from_values(
        enabled=True,
        model=llm_config.model,
        keyword_min_words=llm_config.keyword_min_words,
        summary_min_words=llm_config.summary_min_words,
        summary_word_ratio=llm_config.summary_word_ratio,
        max_keywords=llm_config.max_keywords,
    )
    return representation_profile == _representation_profile(legacy_default_config)


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
    if metadata.get("cache_version") != CACHE_VERSION or not _cache_profile_matches(
        str(metadata.get("cache_profile") or ""),
        cache_profile,
    ):
        return None

    document_id = str(payload.get("document_id") or document_dir.name)
    payload["pdf_url"] = f"/api/documents/{document_id}/file"
    payload["content_hash"] = document_id
    payload["provider"] = metadata.get("provider") or provider_dir.name
    return merge_completed_representations(payload, provider_dir)


def _cache_profile(provider: str, llm_config: LlmRepresentationConfig) -> str:
    semantic_mode = "opendataloader-llm" if provider == "opendataloader" and llm_config.enabled else "heuristic"
    semantic_model = llm_config.model if semantic_mode == "opendataloader-llm" else "none"
    return "|".join(
        [
            CACHE_VERSION,
            f"provider={provider}",
            f"semantic={semantic_mode}",
            f"semantic_model={semantic_model}",
        ]
    )


def _representation_profile(llm_config: LlmRepresentationConfig) -> str:
    if not llm_config.enabled:
        return "placeholder"

    payload = {
        "mode": "llm",
        "model": llm_config.model,
        "keyword_min_words": llm_config.keyword_min_words,
        "summary_min_words": llm_config.summary_min_words,
        "summary_word_ratio": llm_config.summary_word_ratio,
        "max_keywords": llm_config.max_keywords,
        "definitions": [definition.to_dict() for definition in llm_config.representations],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _cache_profile_matches(stored_profile: str, current_profile: str) -> bool:
    """Accept current parser cache profiles and compatible legacy profiles."""

    if stored_profile == current_profile:
        return True

    stored = _cache_profile_parts(stored_profile)
    current = _cache_profile_parts(current_profile)
    if not stored or not current:
        return False
    if stored.get("version") != current.get("version"):
        return False
    if stored.get("provider") != current.get("provider"):
        return False
    if stored.get("semantic") != current.get("semantic"):
        return False
    if current.get("semantic") == "opendataloader-llm":
        stored_model = stored.get("semantic_model") or stored.get("model")
        return stored_model == current.get("semantic_model")
    return True


def _cache_profile_parts(profile: str) -> dict[str, str]:
    parts = [part for part in str(profile or "").split("|") if part]
    if not parts:
        return {}
    parsed = {"version": parts[0]}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed[key] = value
    return parsed


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


def _request_representation_definitions(
    definitions: list[RepresentationDefinitionRequest] | None,
) -> list[RepresentationDefinition] | None:
    if definitions is None:
        return None
    return [definition.to_definition() for definition in definitions]


def _parse_form_representation_definitions(value: str | None) -> list[RepresentationDefinition] | None:
    if not value:
        return None
    try:
        raw_definitions = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid representations JSON.") from error
    if not isinstance(raw_definitions, list):
        raise HTTPException(status_code=400, detail="Representations must be a JSON list.")
    return [
        RepresentationDefinitionRequest.model_validate(raw_definition).to_definition()
        for raw_definition in raw_definitions
        if isinstance(raw_definition, dict)
    ]


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


def _read_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Stored document JSON is unreadable.") from error


def _clear_payload_representations(payload: dict[str, object]) -> None:
    for block in _safe_list(payload.get("blocks")):
        if isinstance(block, dict):
            block["representations"] = []


def _restore_payload_placeholder_representations(payload: dict[str, object]) -> None:
    for block in _safe_list(payload.get("blocks")):
        if not isinstance(block, dict):
            continue
        block["representations"] = [
            representation.to_dict()
            for representation in build_default_block_representations(str(block.get("text") or ""))
        ]


def _provider_status_from_payload(payload: dict[str, object], *, cache_profile: str) -> dict[str, object]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "status": "parsed",
        "document_json": f"providers/{payload.get('provider') or metadata.get('provider')}/document.json",
        "parser": metadata.get("parser"),
        "paragraph_count": metadata.get("paragraph_count"),
        "llm_representations": metadata.get("llm_representations"),
        "cache_version": CACHE_VERSION,
        "cache_profile": cache_profile,
        "representation_profile": metadata.get("representation_profile"),
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _generate_quiz_questions(text: str, api_key: str, model: str) -> list[dict[str, object]]:
    """Call OpenAI to generate 3 SAT-style multiple-choice questions from document text."""

    truncated = text[:6000]
    timeout = float(os.environ.get("OPENAI_REQUEST_TIMEOUT_SECONDS") or 90)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_body: dict[str, object] = {
        "model": model,
        "instructions": (
            "You are a quiz generator for a reading comprehension research study. "
            "Given the article text, generate exactly 3 multiple-choice questions that test "
            "understanding of key facts, findings, or claims stated in the text. "
            "Each question must have exactly 5 answer choices. Exactly one choice is correct. "
            "Be CONCISE: keep each question under 20 words and each choice under 12 words. "
            "Make wrong choices plausible but clearly contradicted by the text. "
            "answer_index is the 0-based index of the correct choice. "
            "Return JSON matching the provided schema exactly."
        ),
        "input": truncated,
        "max_output_tokens": 16384,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "quiz_questions",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "choices": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "answer_index": {"type": "integer"},
                                },
                                "required": ["text", "choices", "answer_index"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["questions"],
                    "additionalProperties": False,
                },
            }
        },
    }

    # For reasoning models the reasoning tokens count against max_output_tokens.
    # Request low effort to minimise that overhead. Non-reasoning models may
    # reject the field with 400; we retry without it in that case.
    body_with_reasoning = {**base_body, "reasoning": {"effort": "low"}}

    output_text = _quiz_call_with_fallback(
        body_primary=body_with_reasoning,
        body_fallback=base_body,
        headers=headers,
        timeout=timeout,
    )

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as error:
        raise ValueError("Quiz generation returned invalid JSON.") from error

    return (parsed.get("questions") or [])[:3]


def _quiz_call_with_fallback(
    *,
    body_primary: dict[str, object],
    body_fallback: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> str:
    """POST to the OpenAI Responses API; on 400 retry without unsupported fields."""

    with httpx.Client(timeout=timeout) as client:
        try:
            response = client.post(OPENAI_RESPONSES_URL, headers=headers, json=body_primary)
            if response.status_code == 400:
                response = client.post(OPENAI_RESPONSES_URL, headers=headers, json=body_fallback)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ValueError(f"Quiz generation failed (HTTP {error.response.status_code}).") from error
        except httpx.HTTPError as error:
            raise ValueError(f"Quiz generation request failed: {error}") from error

    return _extract_output_text(response.json())


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

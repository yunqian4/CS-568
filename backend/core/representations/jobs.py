"""Cached background jobs for paragraph block representations."""

from __future__ import annotations

import json
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import PdfBlock, PdfBlockRepresentation, PdfDocument
from . import llm as llm_module
from .llm import LlmRepresentationConfig

STATUS_NAME = "status.json"


@dataclass(slots=True)
class RepresentationJob:
    """A single representation generation task."""

    job_id: str
    block_id: str
    kind: str


def initialize_representation_jobs(
    document: PdfDocument,
    config: LlmRepresentationConfig,
    provider_dir: Path,
) -> dict[str, Any]:
    """Create pending representation jobs for eligible paragraph blocks."""

    jobs_dir = _jobs_dir(provider_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    if not config.enabled:
        snapshot = _empty_snapshot(enabled=False)
        _write_json(jobs_dir / STATUS_NAME, snapshot)
        return snapshot

    jobs: list[dict[str, Any]] = []
    for block in document.blocks:
        task = llm_module._build_block_task(block, config)
        for kind in task["tasks"]:
            jobs.append(
                {
                    "job_id": f"{block.block_id}:{kind}",
                    "block_id": block.block_id,
                    "kind": kind,
                    "status": "pending",
                    "updated_at": _now(),
                }
            )

    status = {
        "enabled": True,
        "model": config.model,
        "key_source": "user" if config.api_key else "default",
        "status": "pending" if jobs else "complete",
        "total_jobs": len(jobs),
        "completed_jobs": 0,
        "failed_jobs": 0,
        "running_jobs": 0,
        "pending_jobs": len(jobs),
        "jobs": jobs,
        "updated_at": _now(),
    }
    _write_json(jobs_dir / STATUS_NAME, status)
    return _public_status(status)


def run_representation_jobs(
    document: PdfDocument,
    config: LlmRepresentationConfig,
    provider_dir: Path,
    retry_failed: bool = False,
) -> None:
    """Generate pending representation jobs and persist each completed result."""

    if not config.enabled:
        return

    api_key = config.api_key or (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        _mark_all_pending_failed(provider_dir, "LLM representation generation requires OPENAI_API_KEY.")
        return

    jobs_dir = _jobs_dir(provider_dir)
    status = _read_status(jobs_dir)
    blocks_by_id = {block.block_id: block for block in document.blocks}
    max_workers = max(1, int(config.parallel_jobs or 1))
    in_flight: dict[Future, dict[str, Any]] = {}

    def complete_finished(finished: set[Future]) -> None:
        for future in finished:
            job = in_flight.pop(future)
            block_id = str(job.get("block_id"))
            kind = str(job.get("kind"))
            try:
                representation = future.result()
                _write_representation(jobs_dir, block_id, kind, representation)
                job.update({"status": "complete", "updated_at": _now()})
            except Exception as error:  # pragma: no cover - defensive status capture
                job.update({"status": "failed", "error": str(error), "updated_at": _now()})
            _write_status(jobs_dir, status)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for job in status.get("jobs", []):
            if job.get("status") != "pending" and not (retry_failed and job.get("status") == "failed"):
                continue

            block = blocks_by_id.get(str(job.get("block_id")))
            kind = str(job.get("kind"))
            if block is None:
                job.update({"status": "failed", "error": "Block no longer exists.", "updated_at": _now()})
                _write_status(jobs_dir, status)
                continue

            job.pop("error", None)
            job.update({"status": "running", "updated_at": _now()})
            _write_status(jobs_dir, status)
            future = executor.submit(_generate_representation, api_key=api_key, config=config, block=block, kind=kind)
            in_flight[future] = job

            if len(in_flight) >= max_workers:
                finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                complete_finished(finished)

        while in_flight:
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            complete_finished(finished)


def representation_snapshot(provider_dir: Path) -> dict[str, Any]:
    """Return completed representations plus public job status."""

    jobs_dir = _jobs_dir(provider_dir)
    status = _read_status(jobs_dir)
    representations: list[dict[str, Any]] = []
    for job in status.get("jobs", []):
        if job.get("status") != "complete":
            continue
        result_path = _representation_path(jobs_dir, str(job["block_id"]), str(job["kind"]))
        if not result_path.exists():
            continue
        try:
            representations.append(json.loads(result_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    return {**_public_status(status), "representations": representations}


def reset_failed_representation_jobs(provider_dir: Path) -> dict[str, Any]:
    """Move failed or interrupted running jobs back to pending before retry."""

    jobs_dir = _jobs_dir(provider_dir)
    status = _read_status(jobs_dir)
    changed = False
    for job in status.get("jobs", []):
        if job.get("status") not in {"failed", "running"}:
            continue
        job.pop("error", None)
        job.update({"status": "pending", "updated_at": _now()})
        changed = True
    if changed:
        _write_status(jobs_dir, status)
    return _public_status(status)


def merge_completed_representations(document_payload: dict[str, Any], provider_dir: Path) -> dict[str, Any]:
    """Merge cached representation files into a serialized document payload."""

    snapshot = representation_snapshot(provider_dir)
    by_block: dict[str, list[dict[str, Any]]] = {}
    for item in snapshot["representations"]:
        by_block.setdefault(str(item["block_id"]), []).append(item["representation"])

    for block in document_payload.get("blocks", []):
        block_representations = by_block.get(str(block.get("block_id")), [])
        if block_representations:
            block["representations"] = block_representations
    document_payload.setdefault("metadata", {})["llm_representations"] = {
        key: value for key, value in snapshot.items() if key != "representations"
    }
    return document_payload


def _generate_representation(
    *,
    api_key: str,
    config: LlmRepresentationConfig,
    block: PdfBlock,
    kind: str,
) -> PdfBlockRepresentation:
    task = llm_module._build_block_task(block, config)
    task["tasks"] = [kind]
    generated = llm_module._call_openai_representation(
        api_key=api_key,
        config=config,
        task=task,
        kind=kind,
    )
    representations = llm_module._representations_for_block(task=task, generated=generated, config=config)
    if not representations:
        raise ValueError(f"OpenAI did not generate {kind} for {block.block_id}.")
    return representations[0]


def _write_representation(jobs_dir: Path, block_id: str, kind: str, representation: PdfBlockRepresentation) -> None:
    path = _representation_path(jobs_dir, block_id, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        path,
        {
            "block_id": block_id,
            "kind": kind,
            "representation": representation.to_dict(),
            "updated_at": _now(),
        },
    )


def _mark_all_pending_failed(provider_dir: Path, error: str) -> None:
    jobs_dir = _jobs_dir(provider_dir)
    status = _read_status(jobs_dir)
    for job in status.get("jobs", []):
        if job.get("status") in {"pending", "running"}:
            job.update({"status": "failed", "error": error, "updated_at": _now()})
    _write_status(jobs_dir, status)


def _write_status(jobs_dir: Path, status: dict[str, Any]) -> None:
    statuses = [job.get("status") for job in status.get("jobs", [])]
    status["completed_jobs"] = statuses.count("complete")
    status["failed_jobs"] = statuses.count("failed")
    status["running_jobs"] = statuses.count("running")
    status["pending_jobs"] = statuses.count("pending")
    if status.get("total_jobs", 0) == status["completed_jobs"] + status["failed_jobs"]:
        status["status"] = "complete" if status["failed_jobs"] == 0 else "failed"
    else:
        status["status"] = "pending"
    status["updated_at"] = _now()
    _write_json(jobs_dir / STATUS_NAME, status)


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    errors = [
        {
            "block_id": job.get("block_id"),
            "kind": job.get("kind"),
            "error": job.get("error"),
        }
        for job in status.get("jobs", [])
        if job.get("status") == "failed" and job.get("error")
    ]
    return {
        "enabled": bool(status.get("enabled")),
        "model": status.get("model"),
        "key_source": status.get("key_source"),
        "status": status.get("status", "disabled"),
        "total_jobs": int(status.get("total_jobs") or 0),
        "completed_jobs": int(status.get("completed_jobs") or 0),
        "failed_jobs": int(status.get("failed_jobs") or 0),
        "running_jobs": int(status.get("running_jobs") or 0),
        "pending_jobs": int(status.get("pending_jobs") or 0),
        "errors": errors[:10],
    }


def _empty_snapshot(enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "model": None,
        "status": "disabled",
        "total_jobs": 0,
        "completed_jobs": 0,
        "failed_jobs": 0,
        "running_jobs": 0,
        "pending_jobs": 0,
        "jobs": [],
        "updated_at": _now(),
    }


def _read_status(jobs_dir: Path) -> dict[str, Any]:
    path = jobs_dir / STATUS_NAME
    if not path.exists():
        return _empty_snapshot(enabled=False)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_snapshot(enabled=False)


def _jobs_dir(provider_dir: Path) -> Path:
    return provider_dir / "llm" / "representations"


def _representation_path(jobs_dir: Path, block_id: str, kind: str) -> Path:
    return jobs_dir / block_id / f"{kind}.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

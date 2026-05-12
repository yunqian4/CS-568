"""Stage 3: export study cache, public questions, private keys, and scheduler."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from study_pipeline import (
    DOCUMENT_DIR,
    EXAM_DIR,
    MAX_PAGES_ASSET_BYTES,
    PRIVATE_R2_DIR,
    PUBLIC_CACHE,
    SCHEDULER_PATH,
    load_json,
    reset_dir,
    write_json,
)


def main() -> None:
    """Export editable human-study files into deployment-ready assets."""

    args = parse_args()
    export_cloudflare(study_id=args.study_id, title=args.title)


def export_cloudflare(*, study_id: str, title: str) -> None:
    """Build the static cache consumed by the `/study` route."""

    exams = load_exams()
    scheduler = load_json(SCHEDULER_PATH)
    reset_dir(PUBLIC_CACHE / "documents")
    reset_dir(PUBLIC_CACHE / "questions")
    reset_dir(PRIVATE_R2_DIR / "study-private" / "answer-keys")

    document_ids = sorted({exam["document_id"] for exam in exams})
    public_documents = [export_document(document_id) for document_id in document_ids]
    public_exams = [export_exam(exam) for exam in exams]

    manifest = {
        "study_id": study_id,
        "title": title,
        "documents": public_documents,
        "exam_settings": public_exams,
        "scheduler": scheduler,
    }
    write_json(PUBLIC_CACHE / "manifest.json", manifest)
    print(f"Exported {len(public_documents)} documents and {len(public_exams)} exam settings.")
    print(f"Public cache: {PUBLIC_CACHE}")
    print(f"Private R2 keys: {PRIVATE_R2_DIR}")


def load_exams() -> list[dict[str, Any]]:
    """Read enabled exam-setting JSON files."""

    exams = []
    for path in sorted(EXAM_DIR.glob("*.json")):
        exam = load_json(path)
        if exam.get("enabled", True):
            exams.append(exam)
    if not exams:
        raise ValueError(f"No enabled exam settings found in {EXAM_DIR}")
    return exams


def export_document(document_id: str) -> dict[str, Any]:
    """Copy one editable document into the public study cache."""

    source_dir = DOCUMENT_DIR / document_id
    source_pdf = source_dir / "source.pdf"
    document_path = source_dir / "document.json"
    if not source_pdf.exists() or not document_path.exists():
        raise FileNotFoundError(f"Missing prepared document files for {document_id}")
    if source_pdf.stat().st_size > MAX_PAGES_ASSET_BYTES:
        raise ValueError(f"{source_pdf} exceeds the 25 MiB Cloudflare Pages asset limit.")

    payload = merge_workspace_representations(load_json(document_path), source_dir)
    payload["pdf_url"] = f"/study-cache/documents/{document_id}/source.pdf"
    payload["study_document_id"] = document_id

    target = PUBLIC_CACHE / "documents" / document_id
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_pdf, target / "source.pdf")
    write_json(target / "document.json", payload)
    return {
        "document_id": document_id,
        "document_url": f"/study-cache/documents/{document_id}/document.json",
        "title": payload.get("title") or document_id,
    }


def merge_workspace_representations(payload: dict[str, Any], source_dir: Path) -> dict[str, Any]:
    """Merge editable representation files into public document JSON when present."""

    representation_dir = source_dir / "providers" / "opendataloader" / "llm" / "representations"
    status_path = representation_dir / "status.json"
    if status_path.exists():
        payload.setdefault("metadata", {})["llm_representations"] = load_json(status_path)

    by_block: dict[str, list[dict[str, Any]]] = {}
    for path in representation_dir.glob("*/*.json"):
        item = load_json(path)
        representation = item.get("representation")
        block_id = str(item.get("block_id") or path.parent.name)
        if isinstance(representation, dict):
            by_block.setdefault(block_id, []).append(representation)

    for block in payload.get("blocks", []):
        if not isinstance(block, dict):
            continue
        block_reps = by_block.get(str(block.get("block_id")))
        if block_reps:
            block["representations"] = block_reps
    return payload


def export_exam(exam: dict[str, Any]) -> dict[str, Any]:
    """Export one exam setting and split public questions from private answers."""

    question_set_id = str(exam["question_set_id"])
    public_questions = []
    private_answers = []
    for question in exam.get("questions", []):
        question_id = str(question["id"])
        public_questions.append(
            {
                "choices": question["choices"],
                "id": question_id,
                "text": question["text"],
            }
        )
        private_answers.append(
            {
                "answer_index": int(question["answer_index"]),
                "id": question_id,
            }
        )

    write_json(
        PUBLIC_CACHE / "questions" / f"{question_set_id}.json",
        {"question_set_id": question_set_id, "questions": public_questions},
    )
    write_json(
        PRIVATE_R2_DIR / "study-private" / "answer-keys" / f"{question_set_id}.json",
        {"question_set_id": question_set_id, "answers": private_answers},
    )

    public_exam = {
        "document_id": exam["document_id"],
        "id": exam["id"],
        "question_set_id": question_set_id,
        "question_url": f"/study-cache/questions/{question_set_id}.json",
        "questionnaires": exam.get("questionnaires", {}),
        "representation_condition": exam.get("representation_condition", {}),
        "time_limit_seconds": int(exam.get("time_limit_seconds") or 0),
        "title": exam.get("title") or exam["id"],
    }
    return public_exam


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-id", default="cached-pdfreader-v1")
    parser.add_argument("--title", default="Cached PDFReader Study")
    return parser.parse_args()


if __name__ == "__main__":
    main()

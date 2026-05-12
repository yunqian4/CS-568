"""Stage 1: extract chunks and paragraph blocks into editable JSON."""

from __future__ import annotations

import argparse
import shutil

from study_pipeline import (
    document_ids,
    document_workspace,
    load_document_config,
    require_llm_key,
    semantic_config,
    source_pdf_for,
    write_editable_intermediates,
)
from backend import app as backend_app


def main() -> None:
    """Run extraction for one or all PDFs in human-study/pdfs."""

    args = parse_args()
    selected = document_ids(args.document)
    if not selected:
        raise SystemExit("No PDFs found. Add files under human-study/pdfs/<document-id>/source.pdf.")

    for document_id in selected:
        extract_document(document_id, force=args.force)


def extract_document(document_id: str, *, force: bool = False) -> None:
    """Extract one document into editable intermediate files."""

    workspace = document_workspace(document_id)
    if workspace.exists() and not force:
        raise SystemExit(f"{workspace} already exists. Use --force to replace it.")

    source_pdf = source_pdf_for(document_id)
    if not source_pdf.exists():
        raise FileNotFoundError(f"Missing PDF: {source_pdf}")

    config = load_document_config(document_id)
    llm_config = semantic_config(config)
    require_llm_key(llm_config, "Semantic extraction")

    source_name = str(config.get("source_name") or source_pdf.name)
    payload = backend_app._store_and_parse_pdf(
        source_name=source_name,
        pdf_bytes=source_pdf.read_bytes(),
        provider=str(config.get("provider") or "opendataloader"),
        llm_config=llm_config,
        background_tasks=None,
    )
    payload["pdf_url"] = f"/study-cache/documents/{document_id}/source.pdf"
    payload["study_document_id"] = document_id

    if workspace.exists():
        shutil.rmtree(workspace)
    write_editable_intermediates(document_id, payload, config, source_pdf)
    print(f"Extracted {document_id} into {workspace}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", help="Document id under human-study/pdfs/. Defaults to all PDFs.")
    parser.add_argument("--force", action="store_true", help="Replace existing editable intermediates.")
    return parser.parse_args()


if __name__ == "__main__":
    main()

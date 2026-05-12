"""Stage 2: generate representations from editable document JSON."""

from __future__ import annotations

import argparse

from study_pipeline import document_ids, load_document_config, run_representation_generation


def main() -> None:
    """Generate representations for one or all extracted documents."""

    args = parse_args()
    selected = document_ids(args.document)
    if not selected:
        raise SystemExit("No documents found. Run 1_extract_document.py first.")

    for document_id in selected:
        payload = run_representation_generation(document_id, load_document_config(document_id))
        status = payload.get("metadata", {}).get("llm_representations", {})
        print(
            f"Generated {document_id}: "
            f"{status.get('status')} {status.get('completed_jobs')}/{status.get('total_jobs')} complete, "
            f"{status.get('failed_jobs')} failed"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", help="Document id under human-study/documents/. Defaults to all PDFs.")
    return parser.parse_args()


if __name__ == "__main__":
    main()

"""Tests for the local human-study designer API."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import app as backend_app
from backend.core.ingest.contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage


class HumanStudyDesignerTests(unittest.TestCase):
    """Verify editable human-study documents can be loaded and saved."""

    def test_designer_api_loads_and_persists_document_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            human_study_root = Path(temp_dir) / "human-study"
            workspace = human_study_root / "documents" / "sample-doc"
            workspace.mkdir(parents=True)
            (workspace / "source.pdf").write_bytes(b"%PDF-1.4\n")
            _write_json(workspace / "document.json", _sample_document())
            _write_json(workspace / "chunks.json", {"pages": _sample_document()["pages"]})
            _write_json(workspace / "paragraphs.json", {"blocks": _sample_document()["blocks"]})
            _write_json(workspace / "config.json", {"llm": {"representations": []}})

            original_root = backend_app.HUMAN_STUDY_ROOT
            backend_app.HUMAN_STUDY_ROOT = human_study_root
            try:
                client = TestClient(backend_app.app)

                listing = client.get("/api/human-study/documents")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["documents"][0]["document_id"], "sample-doc")

                loaded = client.get("/api/human-study/documents/sample-doc")
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(
                    loaded.json()["document"]["pdf_url"],
                    "/api/human-study/documents/sample-doc/file",
                )

                document = loaded.json()["document"]
                document["blocks"][0]["chunk_ids"] = ["chunk-1"]
                saved = client.post(
                    "/api/human-study/documents/sample-doc",
                    json={
                        "chunks": {"pages": document["pages"]},
                        "document": document,
                        "paragraphs": {"blocks": document["blocks"]},
                    },
                )
                self.assertEqual(saved.status_code, 200)
                persisted = json.loads((workspace / "document.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["pdf_url"], "/study-cache/documents/sample-doc/source.pdf")
                self.assertEqual(persisted["blocks"][0]["block_id"], "paragraph-1")
                self.assertEqual(persisted["pages"][0]["chunks"][0]["block_ids"], ["paragraph-1"])

                reps = client.post(
                    "/api/human-study/documents/sample-doc/representations",
                    json={
                        "representations": [
                            {
                                "background_color": "#263238",
                                "background_opacity": 1,
                                "enabled": True,
                                "name": "summary",
                                "prompt": "Summarize the paragraph.",
                            }
                        ]
                    },
                )
                self.assertEqual(reps.status_code, 200)
                config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
                self.assertEqual(config["llm"]["representations"][0]["name"], "summary")
                prompts = json.loads((workspace / "representation-prompts.json").read_text(encoding="utf-8"))
                self.assertEqual(prompts["document_id"], "sample-doc")
                self.assertEqual(prompts["representations"][0]["prompt"], "Summarize the paragraph.")
                representation_file = json.loads((workspace / "representations.json").read_text(encoding="utf-8"))
                self.assertEqual(representation_file["document_id"], "sample-doc")
                self.assertEqual(reps.json()["saved_paths"]["representation-prompts.json"], str(workspace / "representation-prompts.json"))
                self.assertEqual(reps.json()["saved_paths"]["representations.json"], str(workspace / "representations.json"))
            finally:
                backend_app.HUMAN_STUDY_ROOT = original_root

    def test_exam_designer_api_persists_exam_settings(self) -> None:
        with TemporaryDirectory() as temp_dir:
            human_study_root = Path(temp_dir) / "human-study"
            document_workspace = human_study_root / "documents" / "sample-doc"
            document_workspace.mkdir(parents=True)
            _write_json(document_workspace / "document.json", _sample_document())
            (document_workspace / "source.pdf").write_bytes(b"%PDF-1.4\n")

            original_root = backend_app.HUMAN_STUDY_ROOT
            backend_app.HUMAN_STUDY_ROOT = human_study_root
            try:
                client = TestClient(backend_app.app)
                saved = client.post(
                    "/api/human-study/exams/sample-exam",
                    json={
                        "exam": {
                            "document_id": "sample-doc",
                            "id": "sample-exam",
                            "questions": [
                                {
                                    "answer_index": 1,
                                    "choices": ["A", "B"],
                                    "id": "q1",
                                    "text": "Pick B.",
                                }
                            ],
                            "representation_condition": {
                                "visible_representations": ["summary"],
                            },
                            "timing_mode": "stopwatch",
                            "title": "Sample Exam",
                        }
                    },
                )
                self.assertEqual(saved.status_code, 200)
                exam_path = human_study_root / "exams" / "sample-exam.json"
                self.assertTrue(exam_path.exists())
                persisted = json.loads(exam_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["timing_mode"], "stopwatch")
                self.assertEqual(persisted["time_limit_seconds"], 0)
                self.assertEqual(persisted["questions"][0]["id"], "sample-exam-q1")
                self.assertEqual(persisted["representation_condition"]["visible_representations"], ["summary"])

                listing = client.get("/api/human-study/exams")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(listing.json()["exams"][0]["id"], "sample-exam")

                deleted = client.delete("/api/human-study/exams/sample-exam")
                self.assertEqual(deleted.status_code, 200)
                self.assertFalse(exam_path.exists())
            finally:
                backend_app.HUMAN_STUDY_ROOT = original_root

    def test_local_score_submit_accepts_unanswered_questions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            human_study_root = Path(temp_dir) / "human-study"
            answer_dir = human_study_root / "private-r2" / "study-private" / "answer-keys"
            answer_dir.mkdir(parents=True)
            _write_json(
                answer_dir / "sample-questions.json",
                {
                    "answers": [
                        {"answer_index": 1, "id": "q1"},
                        {"answer_index": 0, "id": "q2"},
                    ],
                    "question_set_id": "sample-questions",
                },
            )

            original_root = backend_app.HUMAN_STUDY_ROOT
            backend_app.HUMAN_STUDY_ROOT = human_study_root
            try:
                client = TestClient(backend_app.app)
                response = client.post(
                    "/api/study/score-submit",
                    json={
                        "answers": [
                            {"question_id": "q1", "selected_index": 1},
                            {"question_id": "q2", "selected_index": None},
                        ],
                        "assignment": {"question_set_id": "sample-questions"},
                        "participant_id": "participant-1",
                        "session_id": "session-1",
                        "study_id": "study-1",
                        "timing": {"elapsed_seconds": 3},
                    },
                )
                self.assertEqual(response.status_code, 200)
                score = response.json()["score"]
                self.assertEqual(score["correct"], 1)
                self.assertIsNone(score["details"][1]["selected_index"])
                self.assertTrue((human_study_root / "results" / "study-1").exists())
            finally:
                backend_app.HUMAN_STUDY_ROOT = original_root

    def test_designer_upload_waits_to_generate_representations(self) -> None:
        with TemporaryDirectory() as temp_dir:
            human_study_root = Path(temp_dir) / "human-study"
            original_root = backend_app.HUMAN_STUDY_ROOT
            backend_app.HUMAN_STUDY_ROOT = human_study_root
            try:
                client = TestClient(backend_app.app)
                with (
                    patch("backend.app.parse_pdf_provider_output", return_value=_parsed_document()),
                    patch("backend.app.apply_llm_semantic_grouping") as semantic_grouping,
                ):
                    response = client.post(
                        "/api/human-study/documents/upload",
                        data={"document_id": "designer-doc"},
                        files={"file": ("designer.pdf", b"%PDF-1.4\n%", "application/pdf")},
                    )

                self.assertEqual(response.status_code, 200)
                semantic_grouping.assert_not_called()
                document = response.json()["document"]
                self.assertEqual(document["study_document_id"], "designer-doc")
                self.assertNotIn("llm_semantic", document["metadata"])
                self.assertEqual(document["metadata"]["llm_representations"]["status"], "not_started")
                self.assertEqual(document["blocks"][0]["representations"], [])
                prompt_path = human_study_root / "documents" / "designer-doc" / "representation-prompts.json"
                self.assertTrue(prompt_path.exists())

                with patch(
                    "backend.core.representations.llm._call_openai_representation",
                    side_effect=_fake_representation_call,
                ):
                    generated = client.post(
                        "/api/human-study/documents/designer-doc/representations/generate",
                        json={
                            "api_key": "test-key",
                            "enabled": True,
                            "openai_representation_parallelism": 4,
                            "representations": [
                                {
                                    "background_color": "#7a4a12",
                                    "background_opacity": 1,
                                    "enabled": True,
                                    "name": "keywords",
                                    "prompt": "Extract useful terms.",
                                },
                                {
                                    "background_color": "#263238",
                                    "background_opacity": 1,
                                    "enabled": True,
                                    "name": "summary",
                                    "prompt": "Summarize the paragraph.",
                                },
                            ],
                        },
                    )

                self.assertEqual(generated.status_code, 200)
                generated_block = generated.json()["document"]["blocks"][0]
                self.assertEqual(
                    {item["kind"] for item in generated_block["representations"]},
                    {"keywords", "summary"},
                )
                config = json.loads(
                    (human_study_root / "documents" / "designer-doc" / "config.json").read_text(encoding="utf-8")
                )
                self.assertEqual(config["llm"]["openai_representation_parallelism"], 4)
                status = client.get("/api/human-study/documents/designer-doc/representations/status")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["status"], "complete")
                prompts = json.loads(prompt_path.read_text(encoding="utf-8"))
                self.assertEqual([item["name"] for item in prompts["representations"]], ["keywords", "summary"])
            finally:
                backend_app.HUMAN_STUDY_ROOT = original_root


def _sample_document() -> dict[str, object]:
    return {
        "blocks": [
            {
                "block_id": "paragraph-0001",
                "chunk_ids": [],
                "page_number": 1,
                "representations": [],
                "section_path": ["Body"],
                "text": "Paragraph text.",
            }
        ],
        "document_id": "sample-doc",
        "metadata": {},
        "page_count": 1,
        "pages": [
            {
                "chunks": [
                    {
                        "block_ids": [],
                        "chunk_id": "chunk-1",
                        "height": 0.1,
                        "page_number": 1,
                        "text": "Paragraph text.",
                        "width": 0.8,
                        "x": 0.1,
                        "y": 0.1,
                    }
                ],
                "height": 792,
                "page_number": 1,
                "width": 612,
            }
        ],
        "pdf_url": "/study-cache/documents/sample-doc/source.pdf",
        "provider": "opendataloader",
        "source_name": "sample.pdf",
        "title": "Sample",
    }


def _parsed_document() -> ParsedPdfDocument:
    long_text = (
        "This paragraph contains enough words for keyword and summary generation because the "
        "designer workflow should wait until prompts are edited before it creates visible "
        "representations for a participant-facing cached study document preview. The extra "
        "sentence keeps the paragraph above the default summary threshold used by the backend "
        "representation generator during tests."
    )
    return ParsedPdfDocument(
        title="Designer Document",
        metadata={"provider": "opendataloader"},
        pages=[
            ParsedPdfPage(
                page_number=1,
                width=612,
                height=792,
                chunks=[
                    ParsedPdfChunk(
                        chunk_id="chunk-1",
                        page_number=1,
                        text=long_text,
                        x=0.1,
                        y=0.1,
                        width=0.8,
                        height=0.1,
                        semantic_type="paragraph",
                    )
                ],
            )
        ],
    )


def _fake_representation_call(*, kind: str, **_kwargs):
    if kind == "keywords":
        return {"keywords": ["designer workflow", "cached study"]}
    return {"summary": "The designer workflow delays representation generation until prompts are edited."}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")

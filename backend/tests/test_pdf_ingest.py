"""Tests for semantic PDF parsing and zoomable document construction."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import fitz
import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import app as backend_app
from core.ingest.grobid_service import GrobidServiceConfig, ensure_grobid_service
from core.ingest.grobid_pdf import (
    GrobidBodyUnit,
    extract_grobid_body_units,
    filter_parsed_document_to_grobid_body,
)
from core.ingest.opendataloader_pdf import build_parsed_document_from_opendataloader_json
from core.ingest.opendataloader_pdf import ensure_java_on_path
from core.ingest.contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage
from core.ingest.llm_semantic import (
    LlmSemanticConfig,
    _semantic_input,
    _semantic_instructions,
    apply_llm_semantic_grouping,
)
from core.ingest import parse_pdf_bytes
from core.ingest.builders import build_pdf_document_from_parsed_pdf
from core.representations.llm import (
    LlmRepresentationConfig,
    RepresentationDefinition,
    _estimate_max_output_tokens,
    _estimate_single_max_output_tokens,
    _extract_output_text,
    _parse_single_representation_payload,
    _post_openai_response,
    enrich_document_representations,
)
from core.representations.jobs import initialize_representation_jobs, representation_snapshot, run_representation_jobs


class PdfIngestTests(unittest.TestCase):
    """Verify semantic parsing builds sections, subsections, and paragraphs."""

    def test_parse_pdf_bytes_builds_semantic_tree_with_paragraph_chunk_mapping(self) -> None:
        document = parse_pdf_bytes("doc-1", "semantic-paper.pdf", _build_semantic_pdf_bytes())

        self.assertEqual(document.document_id, "doc-1")
        self.assertEqual(document.title, "Paper Title")
        self.assertEqual(document.page_count, 1)
        self.assertGreaterEqual(len(document.pages[0].chunks), 3)
        self.assertEqual(len(document.blocks), 2)
        self.assertEqual(document.zoomable_document.node_type, "document")
        self.assertEqual(document.zoomable_document.title, "Paper Title")
        self.assertEqual(len(document.zoomable_document.children), 1)

        intro_section = document.zoomable_document.children[0]
        self.assertEqual(intro_section.node_type, "section")
        self.assertEqual(intro_section.title, "1 Introduction")
        self.assertEqual(len(intro_section.children), 2)

        intro_paragraph = document.blocks[0]
        self.assertEqual(
            intro_paragraph.text,
            "This is the first paragraph of the introduction and it continues in the next text block.",
        )
        self.assertEqual(intro_paragraph.section_path, ["1 Introduction"])
        self.assertGreaterEqual(len(intro_paragraph.chunk_ids), 1)

        background_subsection = intro_section.children[1]
        self.assertEqual(background_subsection.node_type, "subsection")
        self.assertEqual(background_subsection.title, "1.1 Background")
        self.assertEqual(len(background_subsection.children), 1)

        background_paragraph = document.blocks[1]
        self.assertEqual(background_paragraph.section_path, ["1 Introduction", "1.1 Background"])
        chunk_map = {chunk.chunk_id: chunk for chunk in document.pages[0].chunks}
        for chunk_id in intro_paragraph.chunk_ids:
            self.assertEqual(chunk_map[chunk_id].block_ids, [intro_paragraph.block_id])
        for chunk_id in background_paragraph.chunk_ids:
            self.assertEqual(chunk_map[chunk_id].block_ids, [background_paragraph.block_id])

    def test_parse_pdf_bytes_keeps_columns_separate_when_building_semantic_paragraphs(self) -> None:
        document = parse_pdf_bytes("doc-2", "two-columns.pdf", _build_two_column_pdf_bytes())

        self.assertEqual(document.page_count, 1)
        self.assertEqual(len(document.pages[0].chunks), 4)
        self.assertEqual(len(document.blocks), 2)
        self.assertEqual(len(document.zoomable_document.children), 1)
        self.assertEqual(document.zoomable_document.children[0].title, "Body")
        self.assertEqual(
            [block.text for block in document.blocks],
            [
                "Left column first block Left column second block",
                "Right column first block Right column second block",
            ],
        )
        self.assertEqual(
            [block.section_path for block in document.blocks],
            [["Body"], ["Body"]],
        )
        self.assertEqual(
            [len(block.chunk_ids) for block in document.blocks],
            [2, 2],
        )

    def test_grobid_body_filter_excludes_front_and_references(self) -> None:
        body_units = extract_grobid_body_units(_build_grobid_tei())

        self.assertEqual(
            [unit.text for unit in body_units],
            [
                "1 Introduction",
                "This body paragraph should remain.",
                "1.1 Background",
                "This background paragraph should remain.",
            ],
        )

        filtered = filter_parsed_document_to_grobid_body(
            parsed_document=_build_parsed_document_with_front_body_and_references(),
            body_units=body_units,
        )
        remaining_text = [chunk.text for page in filtered.pages for chunk in page.chunks]

        self.assertEqual(
            remaining_text,
            [
                "1 Introduction",
                "This body paragraph should remain.",
                "1.1 Background",
                "This background paragraph should remain.",
            ],
        )

    def test_grobid_service_requires_running_service_or_auto_start(self) -> None:
        config = GrobidServiceConfig(url="http://localhost:8070", auto_start=False)

        with patch("core.ingest.grobid_service._is_grobid_ready", return_value=False):
            with self.assertRaisesRegex(ValueError, "GROBID is not running"):
                ensure_grobid_service(config)

    def test_grobid_service_can_auto_start_when_enabled(self) -> None:
        config = GrobidServiceConfig(url="http://localhost:8070", auto_start=True)

        with (
            patch("core.ingest.grobid_service._is_grobid_ready", return_value=False),
            patch("core.ingest.grobid_service._start_grobid_container") as start_container,
            patch("core.ingest.grobid_service._wait_for_grobid", return_value=True),
        ):
            self.assertEqual(ensure_grobid_service(config), "http://localhost:8070")
            start_container.assert_called_once_with(config)

    def test_opendataloader_json_builds_semantic_chunks_with_locations(self) -> None:
        parsed_document = build_parsed_document_from_opendataloader_json(
            source_name="opendataloader-paper.pdf",
            document_json=_build_opendataloader_json(),
            page_sizes={1: (600.0, 800.0)},
        )

        self.assertEqual(parsed_document.title, "OpenDataLoader Paper")
        self.assertEqual(parsed_document.metadata["presegmented_chunks"], True)
        self.assertEqual(len(parsed_document.pages[0].chunks), 2)

        heading = parsed_document.pages[0].chunks[0]
        self.assertEqual(heading.text, "1 Introduction")
        self.assertEqual(heading.semantic_type, "heading")
        self.assertEqual(heading.heading_level, 1)
        self.assertAlmostEqual(heading.x, 0.1)
        self.assertAlmostEqual(heading.y, 0.1)
        self.assertAlmostEqual(heading.width, 0.8)
        self.assertAlmostEqual(heading.height, 0.05)

        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-odl",
            source_name="opendataloader-paper.pdf",
            provider="opendataloader",
            parsed_document=parsed_document,
        )

        self.assertEqual(document.zoomable_document.children[0].title, "1 Introduction")
        self.assertEqual(document.blocks[0].text, "OpenDataLoader body text stays in reading order.")
        self.assertEqual(document.blocks[0].section_path, ["1 Introduction"])
        self.assertEqual(document.metadata["semantic_parser"], "opendataloader-semantic-json-v1")

    def test_opendataloader_java_check_reports_missing_runtime(self) -> None:
        with (
            patch("core.ingest.opendataloader_pdf.shutil.which", return_value=None),
            patch("core.ingest.opendataloader_pdf._java_candidates", return_value=[]),
            self.assertRaisesRegex(ValueError, "requires Java 11"),
        ):
            ensure_java_on_path()

    def test_llm_semantic_grouping_builds_multi_chunk_paragraph_and_ignores_chunks(self) -> None:
        parsed_document = _build_opendataloader_parsed_document_for_llm()
        llm_output = {
            "title": "LLM Paper",
            "ignored_chunk_ids": ["opendataloader-001-0001"],
            "paragraphs": [
                {
                    "paragraph_id": "p-001",
                    "chunk_ids": ["opendataloader-001-0003", "opendataloader-001-0004"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                }
            ],
        }

        with TemporaryDirectory() as temp_root:
            with patch("core.ingest.llm_semantic._call_semantic_llm", return_value=llm_output):
                metadata = apply_llm_semantic_grouping(
                    parsed_document,
                    LlmSemanticConfig.from_values(
                        enabled=True,
                        api_key="test-key",
                        model="test-model",
                        artifact_dir=Path(temp_root),
                    ),
                )
            document = build_pdf_document_from_parsed_pdf(
                document_id="doc-llm-semantic",
                source_name="llm.pdf",
                provider="opendataloader",
                parsed_document=parsed_document,
            )

            self.assertEqual(metadata["paragraph_count"], 1)
            self.assertEqual(document.metadata["semantic_parser"], "opendataloader-llm-semantic-v1")
            self.assertEqual(document.blocks[0].chunk_ids, ["opendataloader-001-0003", "opendataloader-001-0004"])
            self.assertEqual(document.blocks[0].text, "First paragraph part one. First paragraph part two.")
            self.assertEqual(document.blocks[0].section_path, ["1 Introduction"])
            self.assertEqual(document.pages[0].chunks[0].block_ids, [])
            self.assertEqual(document.blocks[0].block_id, "paragraph-0001")
            self.assertEqual((Path(temp_root) / "llm" / "semantic-input.json").exists(), True)
            self.assertEqual((Path(temp_root) / "llm" / "semantic.json").exists(), True)

    def test_llm_semantic_input_preserves_opendataloader_chunk_order(self) -> None:
        parsed_document = ParsedPdfDocument(
            title="Two column provider order",
            pages=[
                ParsedPdfPage(
                    page_number=1,
                    width=600,
                    height=800,
                    chunks=[
                        ParsedPdfChunk(
                            chunk_id="opendataloader-001-0001",
                            page_number=1,
                            text="Left column continuation.",
                            x=0.1,
                            y=0.6,
                            width=0.35,
                            height=0.04,
                        ),
                        ParsedPdfChunk(
                            chunk_id="opendataloader-001-0002",
                            page_number=1,
                            text="Right column top.",
                            x=0.55,
                            y=0.1,
                            width=0.35,
                            height=0.04,
                        ),
                    ],
                )
            ],
            metadata={"parser": "opendataloader-pdf", "presegmented_chunks": True},
        )

        semantic_input = _semantic_input(parsed_document)

        self.assertEqual(
            [chunk["chunk_id"] for chunk in semantic_input["chunks"]],
            ["opendataloader-001-0001", "opendataloader-001-0002"],
        )
        self.assertEqual([chunk["reading_order"] for chunk in semantic_input["chunks"]], [0, 1])

    def test_llm_semantic_grouping_sorts_output_by_provider_reading_order(self) -> None:
        parsed_document = _build_opendataloader_parsed_document_for_llm()
        llm_output = {
            "title": "LLM Paper",
            "ignored_chunk_ids": [],
            "paragraphs": [
                {
                    "paragraph_id": "later",
                    "chunk_ids": ["opendataloader-001-0004"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                },
                {
                    "paragraph_id": "earlier",
                    "chunk_ids": ["opendataloader-001-0003"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                },
            ],
        }

        with patch("core.ingest.llm_semantic._call_semantic_llm", return_value=llm_output):
            apply_llm_semantic_grouping(
                parsed_document,
                LlmSemanticConfig.from_values(enabled=True, api_key="test-key"),
            )

        paragraphs = parsed_document.metadata["llm_semantic_groups"]["paragraphs"]
        self.assertEqual(
            [paragraph["chunk_ids"][0] for paragraph in paragraphs],
            ["opendataloader-001-0003", "opendataloader-001-0004"],
        )

    def test_llm_semantic_prompt_instructs_paragraph_chunk_merging(self) -> None:
        instructions = _semantic_instructions()

        self.assertIn("multiple chunk_ids", instructions)
        self.assertIn("sentence-complete", instructions)
        self.assertIn("starts lowercase", instructions)
        self.assertIn("Do not reorder chunks by bounding boxes", instructions)

    def test_llm_semantic_grouping_rejects_unknown_chunk_ids(self) -> None:
        parsed_document = _build_opendataloader_parsed_document_for_llm()
        llm_output = {
            "title": "LLM Paper",
            "ignored_chunk_ids": [],
            "paragraphs": [
                {
                    "paragraph_id": "p-001",
                    "chunk_ids": ["missing-chunk"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                }
            ],
        }

        with patch("core.ingest.llm_semantic._call_semantic_llm", return_value=llm_output):
            with self.assertRaisesRegex(ValueError, "unknown chunk IDs"):
                apply_llm_semantic_grouping(
                    parsed_document,
                    LlmSemanticConfig.from_values(enabled=True, api_key="test-key"),
                )

    def test_llm_semantic_grouping_splits_chunk_windows(self) -> None:
        parsed_document = _build_opendataloader_parsed_document_for_llm()
        requested_windows: list[list[str]] = []

        def fake_call_semantic_llm(*, api_key, config, semantic_input):
            chunk_ids = [chunk["chunk_id"] for chunk in semantic_input["chunks"]]
            requested_windows.append(chunk_ids)
            body_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id.endswith(("0003", "0004"))]
            ignored_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id.endswith("0001")]
            return {
                "title": "LLM Paper",
                "ignored_chunk_ids": ignored_chunk_ids,
                "paragraphs": [
                    {
                        "paragraph_id": "p",
                        "chunk_ids": body_chunk_ids,
                        "section_path": ["1 Introduction"],
                        "role": "body",
                    }
                ] if body_chunk_ids else [],
            }

        with patch("core.ingest.llm_semantic._call_semantic_llm", side_effect=fake_call_semantic_llm):
            metadata = apply_llm_semantic_grouping(
                parsed_document,
                LlmSemanticConfig(
                    enabled=True,
                    api_key="test-key",
                    model="test-model",
                    window_chunk_limit=2,
                ),
            )

        self.assertEqual(requested_windows, [
            ["opendataloader-001-0001", "opendataloader-001-0002"],
            ["opendataloader-001-0003", "opendataloader-001-0004"],
        ])
        self.assertEqual(metadata["semantic_window_count"], 2)
        self.assertEqual(parsed_document.metadata["llm_semantic_groups"]["paragraphs"][0]["chunk_ids"], [
            "opendataloader-001-0003",
            "opendataloader-001-0004",
        ])

    def test_llm_semantic_grouping_recursively_splits_on_max_output_tokens(self) -> None:
        parsed_document = _build_opendataloader_parsed_document_for_llm()
        requested_sizes: list[int] = []

        def fake_call_semantic_llm(*, api_key, config, semantic_input):
            chunks = semantic_input["chunks"]
            requested_sizes.append(len(chunks))
            if len(chunks) > 1:
                raise ValueError('OpenAI response was incomplete: {"reason": "max_output_tokens"}')
            chunk_id = chunks[0]["chunk_id"]
            return {
                "title": "LLM Paper",
                "ignored_chunk_ids": [chunk_id] if chunk_id.endswith("0001") else [],
                "paragraphs": [
                    {
                        "paragraph_id": "p",
                        "chunk_ids": [chunk_id],
                        "section_path": ["1 Introduction"],
                        "role": "body",
                    }
                ] if chunk_id.endswith(("0003", "0004")) else [],
            }

        with patch("core.ingest.llm_semantic._call_semantic_llm", side_effect=fake_call_semantic_llm):
            metadata = apply_llm_semantic_grouping(
                parsed_document,
                LlmSemanticConfig(
                    enabled=True,
                    api_key="test-key",
                    model="test-model",
                    window_chunk_limit=8,
                ),
            )

        self.assertIn(4, requested_sizes)
        self.assertEqual(metadata["semantic_window_count"], 4)
        paragraph_chunk_ids = [
            paragraph["chunk_ids"][0]
            for paragraph in parsed_document.metadata["llm_semantic_groups"]["paragraphs"]
        ]
        self.assertEqual(paragraph_chunk_ids, ["opendataloader-001-0003", "opendataloader-001-0004"])

    def test_llm_semantic_grouping_merges_split_sentence_groups(self) -> None:
        parsed_document = ParsedPdfDocument(
            title="Split sentence paper",
            pages=[
                ParsedPdfPage(
                    page_number=1,
                    width=600,
                    height=800,
                    chunks=[
                        ParsedPdfChunk(
                            chunk_id="opendataloader-001-0001",
                            page_number=1,
                            text="1 Introduction",
                            x=0.1,
                            y=0.1,
                            width=0.8,
                            height=0.04,
                            semantic_type="heading",
                            heading_level=1,
                        ),
                        ParsedPdfChunk(
                            chunk_id="opendataloader-001-0002",
                            page_number=1,
                            text="The proposed method improves retrieval",
                            x=0.1,
                            y=0.2,
                            width=0.8,
                            height=0.04,
                            semantic_type="paragraph",
                        ),
                        ParsedPdfChunk(
                            chunk_id="opendataloader-001-0003",
                            page_number=1,
                            text="by aligning local evidence with global context.",
                            x=0.1,
                            y=0.25,
                            width=0.8,
                            height=0.04,
                            semantic_type="paragraph",
                        ),
                    ],
                )
            ],
            metadata={"parser": "opendataloader-pdf", "presegmented_chunks": True},
        )
        llm_output = {
            "title": "Split sentence paper",
            "ignored_chunk_ids": [],
            "paragraphs": [
                {
                    "paragraph_id": "p-1",
                    "chunk_ids": ["opendataloader-001-0002"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                },
                {
                    "paragraph_id": "p-2",
                    "chunk_ids": ["opendataloader-001-0003"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                },
            ],
        }

        with patch("core.ingest.llm_semantic._call_semantic_llm", return_value=llm_output):
            apply_llm_semantic_grouping(
                parsed_document,
                LlmSemanticConfig.from_values(enabled=True, api_key="test-key"),
            )
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-split-sentence",
            source_name="split.pdf",
            provider="opendataloader",
            parsed_document=parsed_document,
        )

        self.assertEqual(len(document.blocks), 1)
        self.assertEqual(
            document.blocks[0].chunk_ids,
            ["opendataloader-001-0002", "opendataloader-001-0003"],
        )

    def test_store_and_parse_pdf_persists_hash_directory_artifacts(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()
        expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                payload = backend_app._store_and_parse_pdf(
                    source_name="semantic-paper.pdf",
                    pdf_bytes=pdf_bytes,
                    provider="native",
                )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

            document_dir = Path(temp_root) / expected_hash
            provider_document_path = document_dir / "providers" / "native" / "document.json"
            manifest = json.loads((document_dir / "manifest.json").read_text(encoding="utf-8"))
            stored_document = json.loads(provider_document_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["document_id"], expected_hash)
            self.assertEqual(payload["content_hash"], expected_hash)
            self.assertEqual(payload["pdf_url"], f"/api/documents/{expected_hash}/file")
            self.assertEqual((document_dir / "source.pdf").read_bytes(), pdf_bytes)
            self.assertEqual(manifest["source_pdf"], "source.pdf")
            self.assertEqual(manifest["content_sha256"], expected_hash)
            self.assertEqual(manifest["providers"]["native"]["document_json"], "providers/native/document.json")
            self.assertEqual(manifest["providers"]["native"]["cache_version"], backend_app.CACHE_VERSION)
            self.assertEqual(stored_document["document_id"], expected_hash)
            self.assertEqual(stored_document["provider"], "native")
            self.assertEqual(stored_document["metadata"]["cache_version"], backend_app.CACHE_VERSION)

    def test_store_and_parse_pdf_reuses_current_cache_for_same_pdf(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                first_payload = backend_app._store_and_parse_pdf(
                    source_name="semantic-paper.pdf",
                    pdf_bytes=pdf_bytes,
                    provider="native",
                )
                with patch("backend.app.parse_pdf_provider_output", side_effect=AssertionError("cache was not used")):
                    second_payload = backend_app._store_and_parse_pdf(
                        source_name="semantic-paper.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="native",
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

        self.assertEqual(second_payload["document_id"], first_payload["document_id"])
        self.assertEqual(second_payload["metadata"]["cache_version"], backend_app.CACHE_VERSION)

    def test_llm_import_returns_without_placeholder_block_representations(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                payload = backend_app._store_and_parse_pdf(
                    source_name="semantic-paper.pdf",
                    pdf_bytes=pdf_bytes,
                    provider="native",
                    llm_config=backend_app.LlmRepresentationConfig.from_values(
                        enabled=True,
                        api_key="test-key",
                        keyword_min_words=3,
                        summary_min_words=5,
                    ),
                    background_tasks=backend_app.BackgroundTasks(),
                )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

        self.assertTrue(payload["metadata"]["llm_representations"]["enabled"])
        self.assertGreater(payload["metadata"]["llm_representations"]["total_jobs"], 0)
        self.assertEqual([block["representations"] for block in payload["blocks"]], [[], []])

    def test_cached_llm_import_retries_unfinished_representation_jobs(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()
        expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

        def fake_call_openai_representation(*, api_key, config, task, kind):
            self.assertEqual(api_key, "retry-key")
            return {"keywords": ["generated keyword"]} if kind == "keywords" else {"summary": "Generated summary from retry."}

        config = backend_app.LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="retry-key",
            keyword_min_words=3,
            summary_min_words=5,
        )

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                first_payload = backend_app._store_and_parse_pdf(
                    source_name="semantic-paper.pdf",
                    pdf_bytes=pdf_bytes,
                    provider="native",
                    llm_config=config,
                    background_tasks=backend_app.BackgroundTasks(),
                )
                provider_dir = Path(temp_root) / expected_hash / "providers" / "native"
                missing_key_config = backend_app.LlmRepresentationConfig.from_values(
                    enabled=True,
                    keyword_min_words=3,
                    summary_min_words=5,
                )
                with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
                    run_representation_jobs(
                        backend_app._document_from_cached_payload(first_payload),
                        missing_key_config,
                        provider_dir,
                    )

                with (
                    patch("backend.app.parse_pdf_provider_output", side_effect=AssertionError("cache was not used")),
                    patch(
                        "backend.core.representations.llm._call_openai_representation",
                        side_effect=fake_call_openai_representation,
                    ),
                ):
                    second_payload = backend_app._store_and_parse_pdf(
                        source_name="semantic-paper.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="native",
                        llm_config=config,
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

        self.assertEqual(second_payload["document_id"], first_payload["document_id"])
        self.assertEqual(second_payload["metadata"]["llm_representations"]["status"], "complete")
        self.assertTrue(any(block["representations"] for block in second_payload["blocks"]))
        self.assertIn("generated keyword", json.dumps(second_payload["blocks"]))

    def test_cached_opendataloader_import_reuses_semantic_cache_when_representation_prompts_change(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()
        expected_hash = hashlib.sha256(pdf_bytes).hexdigest()
        semantic_output = {
            "title": "LLM Paper",
            "ignored_chunk_ids": ["opendataloader-001-0001"],
            "paragraphs": [
                {
                    "paragraph_id": "p-001",
                    "chunk_ids": ["opendataloader-001-0003", "opendataloader-001-0004"],
                    "section_path": ["1 Introduction"],
                    "role": "body",
                }
            ],
        }

        default_config = backend_app.LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="test-key",
            keyword_min_words=3,
            summary_min_words=5,
        )
        changed_prompt_config = backend_app.LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="test-key",
            keyword_min_words=3,
            summary_min_words=5,
            representations=[
                RepresentationDefinition(
                    name="keywords",
                    prompt="Extract only domain-specific terms.",
                    background_color="#5f3510",
                ),
                RepresentationDefinition(
                    name="summary",
                    prompt="Write a very direct one-sentence takeaway.",
                    background_color="#1f2933",
                    background_opacity=1.0,
                ),
            ],
        )

        def fake_call_openai_representation(*, api_key, config, task, kind):
            return {"keywords": ["semantic cache"]} if kind == "keywords" else {"summary": "Cached summary."}

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                with (
                    patch("backend.app.parse_pdf_provider_output", return_value=_build_opendataloader_parsed_document_for_llm()),
                    patch("backend.core.ingest.llm_semantic._call_semantic_llm", return_value=semantic_output),
                    patch(
                        "backend.core.representations.llm._call_openai_representation",
                        side_effect=fake_call_openai_representation,
                    ),
                ):
                    first_payload = backend_app._store_and_parse_pdf(
                        source_name="opendataloader.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="opendataloader",
                        llm_config=default_config,
                    )

                with patch("backend.app.parse_pdf_provider_output", side_effect=AssertionError("semantic cache was not used")):
                    second_payload = backend_app._store_and_parse_pdf(
                        source_name="opendataloader.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="opendataloader",
                        llm_config=changed_prompt_config,
                        background_tasks=backend_app.BackgroundTasks(),
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

            manifest = json.loads((Path(temp_root) / expected_hash / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(second_payload["document_id"], first_payload["document_id"])
        self.assertEqual(second_payload["metadata"]["cache_profile"], first_payload["metadata"]["cache_profile"])
        self.assertNotEqual(
            second_payload["metadata"]["representation_profile"],
            first_payload["metadata"]["representation_profile"],
        )
        self.assertEqual(second_payload["metadata"]["llm_representations"]["status"], "pending")
        self.assertEqual(second_payload["metadata"]["llm_representations"]["total_jobs"], 2)
        self.assertEqual(second_payload["blocks"][0]["representations"], [])
        self.assertEqual(manifest["providers"]["opendataloader"]["status"], "parsed")

    def test_store_and_parse_pdf_ignores_old_cache_versions(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()
        expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                backend_app._store_and_parse_pdf(
                    source_name="semantic-paper.pdf",
                    pdf_bytes=pdf_bytes,
                    provider="native",
                )
                document_json_path = Path(temp_root) / expected_hash / "providers" / "native" / "document.json"
                payload = json.loads(document_json_path.read_text(encoding="utf-8"))
                payload["metadata"]["cache_version"] = "old-implementation"
                document_json_path.write_text(json.dumps(payload), encoding="utf-8")

                with patch("backend.app.parse_pdf_provider_output", return_value=_build_single_chunk_parsed_document()) as parser:
                    refreshed_payload = backend_app._store_and_parse_pdf(
                        source_name="semantic-paper.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="native",
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

        parser.assert_called_once()
        self.assertEqual(refreshed_payload["metadata"]["cache_version"], backend_app.CACHE_VERSION)
        self.assertEqual(refreshed_payload["blocks"][0]["text"], "Current cache content.")

    def test_store_and_parse_pdf_does_not_persist_user_llm_api_key(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()
        expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

        def fake_call_openai_representation(*, api_key, config, task, kind):
            self.assertEqual(api_key, "secret-test-key")
            return {"keywords": ["semantic parsing"]} if kind == "keywords" else {"summary": "Short generated summary."}

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                with patch(
                    "backend.core.representations.llm._call_openai_representation",
                    side_effect=fake_call_openai_representation,
                ):
                    payload = backend_app._store_and_parse_pdf(
                        source_name="semantic-paper.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="native",
                        llm_config=backend_app.LlmRepresentationConfig.from_values(
                            enabled=True,
                            api_key="secret-test-key",
                            keyword_min_words=3,
                            summary_min_words=5,
                        ),
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

            document_dir = Path(temp_root) / expected_hash
            provider_document_path = document_dir / "providers" / "native" / "document.json"
            manifest = json.loads((document_dir / "manifest.json").read_text(encoding="utf-8"))
            stored_document = json.loads(provider_document_path.read_text(encoding="utf-8"))
            persisted_json = json.dumps({"manifest": manifest, "document": stored_document, "payload": payload})

            self.assertEqual(stored_document["metadata"]["llm_representations"]["key_source"], "user")
            self.assertNotIn("secret-test-key", persisted_json)

    def test_representation_jobs_write_files_and_snapshot_status(self) -> None:
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-representation-jobs",
            source_name="jobs.pdf",
            provider="native",
            parsed_document=ParsedPdfDocument(
                title="jobs.pdf",
                metadata={"presegmented_chunks": True},
                pages=[
                    ParsedPdfPage(
                        page_number=1,
                        width=420,
                        height=560,
                        chunks=[_chunk("chunk-001-0001", " ".join(f"semantic{i}" for i in range(60)))],
                    )
                ],
            ),
        )
        config = LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="test-key",
            keyword_min_words=3,
            summary_min_words=10,
        )
        running_snapshots: list[dict] = []

        def fake_call_openai_representation(*, api_key, config, task, kind):
            running_snapshots.append(representation_snapshot(provider_dir))
            return {"keywords": ["semantic parsing"]} if kind == "keywords" else {"summary": "Short generated summary."}

        with TemporaryDirectory() as temp_root:
            provider_dir = Path(temp_root) / "providers" / "native"
            initial_status = initialize_representation_jobs(document, config, provider_dir)
            with patch("core.representations.llm._call_openai_representation", side_effect=fake_call_openai_representation):
                run_representation_jobs(document, config, provider_dir)
            snapshot = representation_snapshot(provider_dir)

            self.assertEqual(initial_status["total_jobs"], 2)
            self.assertEqual(initial_status["pending_jobs"], 2)
            self.assertTrue(any(item["running_jobs"] > 0 for item in running_snapshots))
            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(snapshot["completed_jobs"], 2)
            self.assertEqual(snapshot["running_jobs"], 0)
            self.assertEqual(snapshot["pending_jobs"], 0)
            self.assertEqual({item["kind"] for item in snapshot["representations"]}, {"keywords", "summary"})
            self.assertTrue((provider_dir / "llm" / "representations" / "block-001-0001" / "keywords.json").exists())
            self.assertTrue((provider_dir / "llm" / "representations" / "block-001-0001" / "summary.json").exists())

    def test_representation_jobs_run_with_bounded_parallelism(self) -> None:
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-representation-parallel",
            source_name="parallel.pdf",
            provider="native",
            parsed_document=ParsedPdfDocument(
                title="parallel.pdf",
                metadata={"presegmented_chunks": True},
                pages=[
                    ParsedPdfPage(
                        page_number=1,
                        width=420,
                        height=560,
                        chunks=[
                            _chunk("chunk-001-0001", " ".join(f"alpha{i}" for i in range(12))),
                            _chunk("chunk-001-0002", " ".join(f"beta{i}" for i in range(12))),
                            _chunk("chunk-001-0003", " ".join(f"gamma{i}" for i in range(12))),
                        ],
                    )
                ],
            ),
        )
        config = LlmRepresentationConfig(
            enabled=True,
            api_key="test-key",
            keyword_min_words=3,
            summary_min_words=1000,
            parallel_jobs=3,
        )
        lock = threading.Lock()
        release = threading.Event()
        active_calls = 0
        max_active_calls = 0
        started_calls = 0

        def fake_call_openai_representation(*, api_key, config, task, kind):
            nonlocal active_calls, max_active_calls, started_calls
            with lock:
                active_calls += 1
                started_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
                if started_calls >= 2:
                    release.set()
            release.wait(timeout=0.2)
            time.sleep(0.01)
            with lock:
                active_calls -= 1
            return {"keywords": [str(task["block_id"])]}

        with TemporaryDirectory() as temp_root:
            provider_dir = Path(temp_root) / "providers" / "native"
            initialize_representation_jobs(document, config, provider_dir)
            with patch("core.representations.llm._call_openai_representation", side_effect=fake_call_openai_representation):
                run_representation_jobs(document, config, provider_dir)

        self.assertGreater(max_active_calls, 1)

    def test_default_opendataloader_llm_import_requires_api_key(self) -> None:
        pdf_bytes = _build_semantic_pdf_bytes()

        with TemporaryDirectory() as temp_root:
            original_root = backend_app.TEMP_DOCUMENT_ROOT
            backend_app.TEMP_DOCUMENT_ROOT = Path(temp_root)
            try:
                with (
                    patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False),
                    patch("backend.app.parse_pdf_provider_output", return_value=_build_opendataloader_parsed_document_for_llm()),
                    self.assertRaisesRegex(backend_app.HTTPException, "requires a user API key"),
                ):
                    backend_app._store_and_parse_pdf(
                        source_name="opendataloader.pdf",
                        pdf_bytes=pdf_bytes,
                        provider="opendataloader",
                        llm_config=backend_app.LlmRepresentationConfig.from_values(enabled=True),
                    )
            finally:
                backend_app.TEMP_DOCUMENT_ROOT = original_root

    def test_env_file_loader_sets_defaults_without_overriding_process_env(self) -> None:
        with TemporaryDirectory() as temp_root:
            env_path = Path(temp_root) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=from-file",
                        "OPENAI_REPRESENTATION_MODEL=\"gpt-5-nano\"",
                        "EMPTY_VALUE=",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENAI_API_KEY": "from-process"}, clear=False):
                os.environ.pop("OPENAI_REPRESENTATION_MODEL", None)
                backend_app._load_env_file(env_path)

                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-process")
                self.assertEqual(os.environ["OPENAI_REPRESENTATION_MODEL"], "gpt-5-nano")
                self.assertNotIn("EMPTY_VALUE", os.environ)

    def test_llm_model_defaults_to_env_then_builtin_fallback(self) -> None:
        with patch.dict(os.environ, {"OPENAI_REPRESENTATION_MODEL": "env-model"}, clear=False):
            env_config = LlmRepresentationConfig.from_values(enabled=True)
        self.assertEqual(env_config.model, "env-model")

        with patch.dict(os.environ, {}, clear=True):
            fallback_config = LlmRepresentationConfig.from_values(enabled=True)
        self.assertEqual(fallback_config.model, "gpt-5-nano")

    def test_openai_output_text_extraction_supports_response_variants(self) -> None:
        self.assertEqual(
            _extract_output_text({"output_text": '{"ok": true}'}),
            '{"ok": true}',
        )
        self.assertEqual(
            _extract_output_text(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"from": "responses"}',
                                }
                            ],
                        }
                    ]
                }
            ),
            '{"from": "responses"}',
        )
        self.assertEqual(
            _extract_output_text({"choices": [{"message": {"content": '{"from": "choices"}'}}]}),
            '{"from": "choices"}',
        )

    def test_openai_output_text_extraction_reports_incomplete_and_refusal(self) -> None:
        with self.assertRaisesRegex(ValueError, "incomplete"):
            _extract_output_text({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})

        with self.assertRaisesRegex(ValueError, "refused"):
            _extract_output_text(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "Cannot comply."}],
                        }
                    ]
                }
            )

    def test_openai_request_retries_rate_limit_responses(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        responses = [
            httpx.Response(429, headers={"retry-after": "0"}, request=request),
            httpx.Response(200, json={"output_text": "{}"}, request=request),
        ]

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def post(self, *args, **kwargs):
                return responses.pop(0)

        config = LlmRepresentationConfig(enabled=True, api_key="test-key", request_retries=1)
        with (
            patch("core.representations.llm.httpx.Client", FakeClient),
            patch("core.representations.llm.time.sleep") as sleep,
        ):
            payload = _post_openai_response(
                api_key="test-key",
                config=config,
                operation="OpenAI test request",
                body={"model": "test-model", "input": "test"},
            )

        self.assertEqual(payload, {"output_text": "{}"})
        sleep.assert_called_once_with(0.0)

    def test_representation_output_token_budget_allows_reasoning_models(self) -> None:
        self.assertGreaterEqual(
            _estimate_max_output_tokens(
                [
                    {
                        "block_id": "block-1",
                        "max_keywords": 5,
                        "summary_target_words": 0,
                    }
                ]
            ),
            2048,
        )
        self.assertEqual(
            _parse_single_representation_payload({"output_text": '{"k":["first","second"]}'}, kind="keywords"),
            {"keywords": ["first", "second"]},
        )
        self.assertEqual(
            _parse_single_representation_payload({"output_text": '{"s":"Short summary."}'}, kind="summary"),
            {"summary": "Short summary."},
        )
        self.assertLessEqual(
            _estimate_single_max_output_tokens({"summary_target_words": 20}, "summary"),
            4096,
        )

    def test_llm_representations_apply_thresholds_and_summary_ratio(self) -> None:
        long_text = " ".join(f"semantic{i}" for i in range(60))
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-llm",
            source_name="llm.pdf",
            provider="native",
            parsed_document=ParsedPdfDocument(
                title="llm.pdf",
                metadata={"presegmented_chunks": True},
                pages=[
                    ParsedPdfPage(
                        page_number=1,
                        width=420,
                        height=560,
                        chunks=[
                            _chunk("chunk-001-0001", "Short text."),
                            _chunk("chunk-001-0002", long_text),
                        ],
                    )
                ],
            ),
        )
        captured_tasks: list[dict] = []

        def fake_call_openai_representation(*, api_key, config, task, kind):
            self.assertEqual(api_key, "test-key")
            captured_tasks.append(task)
            if kind == "keywords":
                return {"keywords": ["semantic parsing", "layout analysis", "extra"]}
            return {"summary": " ".join(f"summary{i}" for i in range(30))}

        config = LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="test-key",
            keyword_min_words=6,
            summary_min_words=40,
            summary_word_ratio=0.25,
            max_keywords=2,
        )
        with patch("core.representations.llm._call_openai_representation", side_effect=fake_call_openai_representation):
            metadata = enrich_document_representations(document, config)

        self.assertEqual(metadata["eligible_blocks"], 1)
        self.assertEqual(metadata["generated_blocks"], 1)
        self.assertEqual(metadata["key_source"], "user")
        self.assertEqual(document.blocks[0].representations, [])
        self.assertEqual(captured_tasks[0]["summary_target_words"], 15)
        representation_by_kind = {item.kind: item for item in document.blocks[1].representations}
        self.assertEqual(representation_by_kind["keywords"].items, ["semantic parsing", "layout analysis"])
        self.assertEqual(representation_by_kind["keywords"].value, "semantic parsing, layout analysis")
        self.assertLessEqual(len(representation_by_kind["summary"].text.split()), 23)

    def test_custom_representation_definition_generates_string_value_and_color(self) -> None:
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-custom-rep",
            source_name="custom.pdf",
            provider="native",
            parsed_document=ParsedPdfDocument(
                title="custom.pdf",
                metadata={"presegmented_chunks": True},
                pages=[
                    ParsedPdfPage(
                        page_number=1,
                        width=420,
                        height=560,
                        chunks=[_chunk("chunk-001-0001", " ".join(f"semantic{i}" for i in range(20)))],
                    )
                ],
            ),
        )

        def fake_call_openai_representation(*, api_key, config, task, kind):
            self.assertEqual(kind, "takeaway")
            self.assertIn("takeaway", task["definitions"])
            return {"takeaway": "Custom generated takeaway."}

        config = LlmRepresentationConfig.from_values(
            enabled=True,
            api_key="test-key",
            summary_min_words=3,
            representations=[
                RepresentationDefinition(
                    name="takeaway",
                    prompt="Write one takeaway.",
                    background_color="#ffeeaa",
                    background_opacity=0.35,
                )
            ],
        )
        with patch("core.representations.llm._call_openai_representation", side_effect=fake_call_openai_representation):
            enrich_document_representations(document, config)

        representation = document.blocks[0].representations[0]
        self.assertEqual(representation.kind, "takeaway")
        self.assertEqual(representation.value, "Custom generated takeaway.")
        self.assertEqual(representation.background_color, "#ffeeaa")
        self.assertEqual(representation.background_opacity, 0.35)

    def test_llm_representations_require_user_or_default_key(self) -> None:
        document = build_pdf_document_from_parsed_pdf(
            document_id="doc-llm-missing-key",
            source_name="llm.pdf",
            provider="native",
            parsed_document=ParsedPdfDocument(
                title="llm.pdf",
                metadata={"presegmented_chunks": True},
                pages=[
                    ParsedPdfPage(
                        page_number=1,
                        width=420,
                        height=560,
                        chunks=[_chunk("chunk-001-0001", " ".join(f"word{i}" for i in range(12)))],
                    )
                ],
            ),
        )
        config = LlmRepresentationConfig.from_values(enabled=True, api_key=None, keyword_min_words=3)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False),
            self.assertRaisesRegex(ValueError, "requires a user API key"),
        ):
            enrich_document_representations(document, config)


def _build_semantic_pdf_bytes() -> bytes:
    document = fitz.open()
    try:
        page = document.new_page(width=420, height=560)
        page.insert_textbox(fitz.Rect(70, 20, 360, 56), "Paper Title", fontsize=20)
        page.insert_textbox(fitz.Rect(28, 92, 220, 120), "1 Introduction", fontsize=16)
        page.insert_textbox(
            fitz.Rect(28, 150, 380, 178),
            "This is the first paragraph of the introduction",
            fontsize=12,
        )
        page.insert_textbox(
            fitz.Rect(28, 196, 380, 224),
            "and it continues in the next text block.",
            fontsize=12,
        )
        page.insert_textbox(fitz.Rect(28, 270, 240, 296), "1.1 Background", fontsize=14)
        page.insert_textbox(
            fitz.Rect(28, 326, 380, 354),
            "Background paragraph text stays in this subsection.",
            fontsize=12,
        )
        return document.tobytes()
    finally:
        document.close()


def _build_two_column_pdf_bytes() -> bytes:
    document = fitz.open()
    try:
        page = document.new_page(width=420, height=520)
        page.insert_textbox(fitz.Rect(28, 42, 176, 70), "Left column first block", fontsize=12)
        page.insert_textbox(fitz.Rect(28, 74, 176, 102), "Left column second block", fontsize=12)
        page.insert_textbox(fitz.Rect(240, 44, 388, 72), "Right column first block", fontsize=12)
        page.insert_textbox(fitz.Rect(240, 76, 388, 104), "Right column second block", fontsize=12)
        return document.tobytes()
    finally:
        document.close()


def _build_grobid_tei() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Ignored title metadata</title></titleStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <front>
      <docAuthor>Jane Author</docAuthor>
      <p>University affiliation should not appear.</p>
    </front>
    <body>
      <div>
        <head>1 Introduction</head>
        <p>This body paragraph should remain.</p>
        <div>
          <head>1.1 Background</head>
          <p>This background paragraph should remain.</p>
        </div>
      </div>
    </body>
    <back>
      <listBibl>
        <biblStruct><monogr><title>Reference should not appear.</title></monogr></biblStruct>
      </listBibl>
    </back>
  </text>
</TEI>"""


def _build_parsed_document_with_front_body_and_references() -> ParsedPdfDocument:
    chunks = [
        _chunk("chunk-001-0001", "Jane Author"),
        _chunk("chunk-001-0002", "University affiliation should not appear."),
        _chunk("chunk-001-0003", "1 Introduction"),
        _chunk("chunk-001-0004", "This body paragraph should remain."),
        _chunk("chunk-001-0005", "1.1 Background"),
        _chunk("chunk-001-0006", "This background paragraph should remain."),
        _chunk("chunk-001-0007", "Reference should not appear."),
    ]
    return ParsedPdfDocument(
        title="sample.pdf",
        pages=[ParsedPdfPage(page_number=1, width=420, height=560, chunks=chunks)],
    )


def _build_opendataloader_json() -> dict:
    return {
        "file name": "opendataloader-paper.pdf",
        "number of pages": 1,
        "title": "OpenDataLoader Paper",
        "kids": [
            {
                "type": "heading",
                "id": 1,
                "page number": 1,
                "bounding box": [60.0, 680.0, 540.0, 720.0],
                "heading level": 1,
                "font": "Helvetica-Bold",
                "font size": 18.0,
                "content": "1 Introduction",
            },
            {
                "type": "paragraph",
                "id": 2,
                "page number": 1,
                "bounding box": [60.0, 620.0, 540.0, 660.0],
                "font": "Helvetica",
                "font size": 11.0,
                "content": "OpenDataLoader body text stays in reading order.",
            },
            {
                "type": "paragraph",
                "id": 3,
                "page number": 1,
                "bounding box": [60.0, 40.0, 540.0, 55.0],
                "font": "Helvetica",
                "font size": 8.0,
                "hidden text": True,
                "content": "Hidden prompt injection should be skipped.",
            },
        ],
    }


def _build_opendataloader_parsed_document_for_llm() -> ParsedPdfDocument:
    return ParsedPdfDocument(
        title="OpenDataLoader LLM Paper",
        pages=[
            ParsedPdfPage(
                page_number=1,
                width=600,
                height=800,
                chunks=[
                    ParsedPdfChunk(
                        chunk_id="opendataloader-001-0001",
                        page_number=1,
                        text="Jane Author",
                        x=0.1,
                        y=0.05,
                        width=0.8,
                        height=0.03,
                        semantic_type="paragraph",
                    ),
                    ParsedPdfChunk(
                        chunk_id="opendataloader-001-0002",
                        page_number=1,
                        text="1 Introduction",
                        x=0.1,
                        y=0.15,
                        width=0.8,
                        height=0.04,
                        semantic_type="heading",
                        heading_level=1,
                    ),
                    ParsedPdfChunk(
                        chunk_id="opendataloader-001-0003",
                        page_number=1,
                        text="First paragraph part one.",
                        x=0.1,
                        y=0.22,
                        width=0.8,
                        height=0.04,
                        semantic_type="paragraph",
                    ),
                    ParsedPdfChunk(
                        chunk_id="opendataloader-001-0004",
                        page_number=1,
                        text="First paragraph part two.",
                        x=0.1,
                        y=0.27,
                        width=0.8,
                        height=0.04,
                        semantic_type="paragraph",
                    ),
                ],
            )
        ],
        metadata={
            "parser": "opendataloader-pdf",
            "presegmented_chunks": True,
            "semantic_source": "opendataloader-json",
        },
    )


def _build_single_chunk_parsed_document() -> ParsedPdfDocument:
    return ParsedPdfDocument(
        title="current.pdf",
        metadata={"presegmented_chunks": True},
        pages=[
            ParsedPdfPage(
                page_number=1,
                width=420,
                height=560,
                chunks=[_chunk("chunk-001-0001", "Current cache content.")],
            )
        ],
    )


def _chunk(chunk_id: str, text: str) -> ParsedPdfChunk:
    chunk_index = int(chunk_id.rsplit("-", 1)[-1])
    return ParsedPdfChunk(
        chunk_id=chunk_id,
        page_number=1,
        text=text,
        x=0.1,
        y=chunk_index * 0.05,
        width=0.8,
        height=0.03,
        font_size=12.0,
    )


if __name__ == "__main__":
    unittest.main()

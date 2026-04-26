"""Docling-based PDF parsing."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.utils.model_downloader import download_models

from .contracts import ParsedPdfChunk, ParsedPdfDocument, ParsedPdfPage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCLING_ARTIFACTS_DIR = PROJECT_ROOT / "dev" / "docling-artifacts"


def parse_docling_pdf_bytes(source_name: str, pdf_bytes: bytes) -> ParsedPdfDocument:
    """Extract chunk boxes from Docling text item provenance."""

    _ensure_docling_artifacts()

    with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(pdf_bytes)

    try:
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=PdfPipelineOptions(
                        artifacts_path=DOCLING_ARTIFACTS_DIR,
                        do_ocr=False,
                        do_table_structure=False,
                        do_code_enrichment=False,
                        do_formula_enrichment=False,
                        generate_page_images=False,
                        generate_picture_images=False,
                        generate_parsed_pages=False,
                        force_backend_text=True,
                    )
                )
            }
        )
        result = converter.convert(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    doc = result.document
    pages: list[ParsedPdfPage] = []
    for page_no, page in sorted(doc.pages.items()):
        page_width = float(page.size.width or 1.0)
        page_height = float(page.size.height or 1.0)
        chunks: list[ParsedPdfChunk] = []
        chunk_index = 1

        for item in doc.texts:
            prov_entries = [prov for prov in getattr(item, "prov", []) if getattr(prov, "page_no", None) == page_no]
            if not prov_entries:
                continue

            text = " ".join(str(getattr(item, "text", "")).split())
            if not text:
                continue

            box = _merge_docling_boxes(prov_entries)
            chunks.append(
                ParsedPdfChunk(
                    chunk_id=f"docling-{page_no:03d}-{chunk_index:04d}",
                    page_number=page_no,
                    text=text,
                    x=_clamp(box["x0"] / page_width),
                    y=_clamp(1.0 - (box["y1"] / page_height)),
                    width=_clamp((box["x1"] - box["x0"]) / page_width),
                    height=_clamp((box["y1"] - box["y0"]) / page_height),
                    font_size=None,
                    font_name=None,
                    is_bold=None,
                )
            )
            chunk_index += 1

        pages.append(ParsedPdfPage(page_number=page_no, width=page_width, height=page_height, chunks=chunks))

    return ParsedPdfDocument(title=source_name, pages=pages, metadata={"parser": "docling"})


def _ensure_docling_artifacts() -> None:
    if (DOCLING_ARTIFACTS_DIR / "model.safetensors").exists():
        return
    DOCLING_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    download_models(
        output_dir=DOCLING_ARTIFACTS_DIR,
        with_layout=True,
        with_tableformer=False,
        with_tableformer_v2=False,
        with_code_formula=False,
        with_picture_classifier=False,
        with_smolvlm=False,
        with_granitedocling=False,
        with_granitedocling_mlx=False,
        with_smoldocling=False,
        with_smoldocling_mlx=False,
        with_granite_vision=False,
        with_granite_chart_extraction=False,
        with_rapidocr=False,
        with_easyocr=False,
        progress=False,
    )


def _merge_docling_boxes(prov_entries: list[object]) -> dict[str, float]:
    x0 = min(float(prov.bbox.l) for prov in prov_entries)
    y0 = min(float(prov.bbox.b) for prov in prov_entries)
    x1 = max(float(prov.bbox.r) for prov in prov_entries)
    y1 = max(float(prov.bbox.t) for prov in prov_entries)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)

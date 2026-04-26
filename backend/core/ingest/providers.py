"""Provider selection for PDF parsing backends."""

from __future__ import annotations

from .builders import build_pdf_document_from_parsed_pdf
from .docling_pdf import parse_docling_pdf_bytes
from .grobid_pdf import parse_grobid_pdf_bytes
from .opendataloader_pdf import parse_opendataloader_pdf_bytes
from .pdf import parse_native_pdf_bytes

PARSER_PROVIDERS = {
    "docling": parse_docling_pdf_bytes,
    "grobid": parse_grobid_pdf_bytes,
    "native": parse_native_pdf_bytes,
    "opendataloader": parse_opendataloader_pdf_bytes,
}


def list_pdf_providers() -> list[str]:
    """Return the supported parser provider names."""

    return sorted(PARSER_PROVIDERS)


def parse_pdf_bytes(document_id: str, source_name: str, pdf_bytes: bytes):
    """Parse a PDF with the default native provider."""

    return parse_pdf_bytes_with_provider(
        document_id=document_id,
        source_name=source_name,
        pdf_bytes=pdf_bytes,
        provider="native",
    )


def parse_pdf_bytes_with_provider(
    document_id: str,
    source_name: str,
    pdf_bytes: bytes,
    provider: str = "native",
):
    """Parse a PDF with the requested backend provider."""

    normalized = provider.lower().strip()
    parser = PARSER_PROVIDERS.get(normalized)
    if parser is None:
        raise ValueError(f"Unsupported PDF provider: {provider}")

    parsed_document = parse_pdf_provider_output(source_name=source_name, pdf_bytes=pdf_bytes, provider=normalized)
    return build_pdf_document_from_parsed_pdf(
        document_id=document_id,
        source_name=source_name,
        provider=normalized,
        parsed_document=parsed_document,
    )


def parse_pdf_provider_output(source_name: str, pdf_bytes: bytes, provider: str = "native"):
    """Parse provider output before zoomable document construction."""

    normalized = provider.lower().strip()
    parser = PARSER_PROVIDERS.get(normalized)
    if parser is None:
        raise ValueError(f"Unsupported PDF provider: {provider}")
    return parser(source_name=source_name, pdf_bytes=pdf_bytes)

"""Placeholder keyword generation for PDF text chunks."""

from __future__ import annotations

import re
from collections import Counter

from ..models.pdf_document import PdfChunkKeyword

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{2,}")
STOP_WORDS = {
    "about",
    "after",
    "also",
    "been",
    "from",
    "into",
    "page",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "were",
    "with",
}


def build_placeholder_keywords(text: str, limit: int = 3) -> list[PdfChunkKeyword]:
    """Return a few stable placeholder keywords from a chunk."""

    candidates = [
        match.group(0).lower()
        for match in WORD_RE.finditer(text)
        if match.group(0).lower() not in STOP_WORDS
    ]
    if not candidates:
        return [PdfChunkKeyword(label="keyword pending")]

    counts = Counter(candidates)
    labels = [word.replace("-", " ") for word, _ in counts.most_common(limit)]
    return [PdfChunkKeyword(label=label) for label in labels]

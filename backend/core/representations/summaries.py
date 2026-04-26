"""Placeholder summary generation for paragraph blocks."""

from __future__ import annotations

import re

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def build_placeholder_summary(text: str, limit: int = 120) -> str:
    """Return a short summary-like preview for a block."""

    compact = " ".join(text.split())
    if not compact:
        return "summary pending"

    sentence = SENTENCE_SPLIT_RE.split(compact, maxsplit=1)[0].strip()
    if len(sentence) <= limit:
        return sentence

    shortened = sentence[:limit].rsplit(" ", 1)[0].strip()
    return (shortened or sentence[:limit]).rstrip(" .,;:") + "..."

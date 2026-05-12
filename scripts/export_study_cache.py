"""Compatibility wrapper for the human-study Cloudflare exporter."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORTER = REPO_ROOT / "human-study" / "scripts" / "3_export_cloudflare.py"


def main() -> None:
    sys.path.insert(0, str(EXPORTER.parent))
    runpy.run_path(str(EXPORTER), run_name="__main__")


if __name__ == "__main__":
    main()

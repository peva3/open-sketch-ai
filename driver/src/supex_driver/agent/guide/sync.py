"""Refresh the bundled guide copies from the repo's canonical guide.

Run from the repository root::

    python -m supex_driver.agent.guide.sync

Copies ``docs/agents/guide/*.md`` into this package so the agent can build its
system prompt outside a checkout. The parity test
``driver/tests/agent/test_guide_bundle.py`` fails when the two drift.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

GUIDE_SUBDIR = "docs/agents/guide"

PACKAGE_DIR = Path(__file__).resolve().parent


def _repo_guide_dir() -> Path:
    current = PACKAGE_DIR
    for parent in (current, *current.parents):
        candidate = parent / GUIDE_SUBDIR
        if candidate.is_dir():
            return candidate.resolve()
    raise SystemExit(
        f"could not find {GUIDE_SUBDIR} walking up from {PACKAGE_DIR}; run from a repo checkout"
    )


def sync() -> None:
    source = _repo_guide_dir()
    count = 0
    for path in sorted(source.glob("*.md")):
        shutil.copyfile(path, PACKAGE_DIR / path.name)
        count += 1
    print(f"copied {count} guide files from {source}")


if __name__ == "__main__":
    sync()
    sys.exit(0)

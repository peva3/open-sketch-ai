"""Parity between the bundled agent guide and the canonical docs/agents/guide.

The agent can assemble its system prompt outside a repo checkout only from the
bundled copies in ``supex_driver.agent.guide``. Those copies must not drift from
the canonical ``docs/agents/guide``; refresh with
``python -m supex_driver.agent.guide.sync`` from the repo root.
"""

from pathlib import Path

from supex_driver.agent import prompts

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDE_DIR = REPO_ROOT / "docs" / "agents" / "guide"
BUNDLE_DIR = Path(prompts.__file__).resolve().parent / "guide"


def test_guide_bundle_matches_repo() -> None:
    assert GUIDE_DIR.is_dir(), f"canonical guide missing: {GUIDE_DIR}"
    for name in prompts.GUIDE_FILES:
        canonical = (GUIDE_DIR / name).read_text(encoding="utf-8")
        bundled = (BUNDLE_DIR / name).read_text(encoding="utf-8")
        assert bundled == canonical, (
            f"bundled guide {name!r} drifted from docs/agents/guide; "
            "run `python -m supex_driver.agent.guide.sync` from the repo root"
        )


def test_bundle_contains_every_canonical_md() -> None:
    canonical_md = {p.name for p in GUIDE_DIR.glob("*.md")}
    bundled_md = {p.name for p in BUNDLE_DIR.glob("*.md")}
    assert canonical_md == bundled_md

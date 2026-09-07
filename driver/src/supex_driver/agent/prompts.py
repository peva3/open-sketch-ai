"""Assemble the supex-chat system prompt from the SketchUp agent guide.

The agent guide lives at ``docs/agents/guide/*.md`` in a repo checkout
(``README.md`` router, ``mcp.md`` canonical tool inventory, ``ruby.md``
and ``vcad.md`` workflow references, ``workflow.md``, and
``troubleshooting.md``). Source resolution is dual:

* In a repo checkout the guide is read straight from disk. The checkout is
  located by walking up from this package's file (works from any cwd) or
  from ``SUPEX_ROOT`` when that env var is set.
* Outside a checkout (installed wheel / frozen binary) the same files are
  read from package data bundled as ``supex_driver.agent.guide`` (see the
  packaging work; Phase 7 of TODO.md).

:func:`build_system_prompt` returns one self-contained prompt string so the
loop only ever sends a single system message regardless of source.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from supex_driver.agent.errors import GuideError

GUIDE_SUBDIR = "docs/agents/guide"
BUNDLED_PACKAGE = "supex_driver.agent.guide"

GUIDE_FILES: tuple[str, ...] = (
    "README.md",
    "mcp.md",
    "ruby.md",
    "vcad.md",
    "workflow.md",
    "troubleshooting.md",
)


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _repo_guide_dir() -> Path | None:
    """Return the checkout guide directory, or None when not in a checkout."""
    env_root = os.environ.get("SUPEX_ROOT")
    if env_root:
        candidate = Path(env_root) / GUIDE_SUBDIR
        if candidate.is_dir():
            return candidate.resolve()
    current = _package_dir()
    for parent in (current, *current.parents):
        candidate = parent / GUIDE_SUBDIR
        if candidate.is_dir():
            return candidate.resolve()
    return None


def resolve_guide_dir() -> Path | None:
    """Expose the on-disk guide dir (repo checkout), or None when bundled-only."""
    return _repo_guide_dir()


def _repo_guide_text(name: str) -> str | None:
    directory = _repo_guide_dir()
    if directory is None:
        return None
    path = directory / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuideError(f"could not read guide file {path}: {exc}") from exc


def _bundled_guide_text(name: str) -> str:
    try:
        return (
            resources.files(BUNDLED_PACKAGE).joinpath(name).read_text(encoding="utf-8")
        )
    except (ModuleNotFoundError, FileNotFoundError, OSError) as exc:
        raise GuideError(
            f"no SketchUp guide found (no repo checkout and bundled guide "
            f"file {name!r} is missing): {exc}"
        ) from exc


def load_guide_file(name: str) -> tuple[str, str]:
    """Return ``(filename, markdown)`` for one guide file, repo then bundled."""
    if Path(name).name != name or not name.endswith(".md"):
        raise GuideError(f"refusing to load non-guide file {name!r}")
    text = _repo_guide_text(name)
    if text is None:
        text = _bundled_guide_text(name)
    return name, text


def guide_available() -> bool:
    """True when guide content resolves from disk or bundled package data."""
    if _repo_guide_dir() is not None:
        return True
    try:
        _bundled_guide_text(GUIDE_FILES[0])
        return True
    except GuideError:
        return False


def _preamble(workspace: str, *, vision: bool) -> str:
    parts = [
        "You are supex-chat, an agent that drives a live SketchUp 2026 "
        "instance through the tools listed below. You may also read and "
        f"write files, but only inside the workspace: {workspace}",
        "Author Ruby scripts as .rb files inside the workspace and evaluate "
        "them with eval_ruby_file (preferred) or eval_ruby for short "
        "snippets. Keep code in files rather than long inline evals.",
        "Verify visual results with screenshots. Tool results are text: JSON "
        "results carry an ok field; screenshots come back as file paths, "
        "never as pixels.",
        "The guide sections that follow are authoritative. Follow ruby.md "
        "for SketchUp edits and workflow.md for the verify loop; treat "
        "mcp.md as the canonical tool reference and troubleshooting.md for "
        "recovery. vcad.md applies only when the VCAD sidecar is reachable.",
    ]
    if vision:
        parts.append(
            "You have image vision in this session. Image content can "
            "accompany user prompts, and images you request by calling a "
            "screenshot tool (take_screenshot, take_batch_screenshots) are "
            "returned to you on your next turn so you can see the actual "
            "result of the code you wrote. Reason about those images when "
            "present. A tool result that is a JSON object with binary:true "
            "means the file is not readable as text."
        )
    else:
        parts.append(
            "You cannot view image files: if you need to see a screenshot, "
            "ask the user to attach it on their next message."
        )
    return "\n\n".join(parts)


def build_system_prompt(
    workspace: str | Path,
    *,
    files: Sequence[str] = GUIDE_FILES,
    vision: bool = False,
) -> str:
    """Build the full system prompt (preamble + bundled guide sections)."""
    workspace_text = str(Path(workspace).resolve())
    sections: list[str] = [_preamble(workspace_text, vision=vision)]
    for name in files:
        filename, markdown = load_guide_file(name)
        sections.append(f"# Guide section: {filename}\n\n{markdown.strip()}")
    return "\n\n".join(sections)

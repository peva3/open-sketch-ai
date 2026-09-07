"""Keep the documented Supex Chat Agent tool surface in sync with the file tools."""

from __future__ import annotations

import re
from pathlib import Path

from supex_driver.agent.file_tools import FileTools
from supex_driver.agent.providers.base import ToolSchema

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DOC = REPO_ROOT / "docs" / "agent.md"
CODE_SPAN = re.compile(r"`([a-z_]+)`")


def documented_file_tools() -> set[str]:
    """Return file tool names mentioned as code spans anywhere in docs/agent.md."""
    text = AGENT_DOC.read_text(encoding="utf-8")
    names = {m.group(1) for m in CODE_SPAN.finditer(text)}
    return names


def test_file_tools_match_documented_inventory() -> None:
    if not AGENT_DOC.exists():
        import pytest

        pytest.skip(f"{AGENT_DOC} not available in this checkout")

    documented = documented_file_tools()
    expected = {
        "delete_file",
        "edit_file",
        "list_dir",
        "read_file",
        "write_file",
    }
    present = expected & documented
    assert present == expected, (
        f"docs/agent.md missing file-tool mentions: {sorted(expected - present)}"
    )


def test_file_tool_schemas_match_tool_names() -> None:
    schemas = FileTools().schemas()
    assert [s.name for s in schemas] == list(FileTools.TOOL_NAMES)
    for schema in schemas:
        assert isinstance(schema, ToolSchema)
        assert schema.input_schema

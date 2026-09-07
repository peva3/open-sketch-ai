"""Tests for system-prompt assembly from the SketchUp agent guide."""

from __future__ import annotations

import pytest

from supex_driver.agent import prompts
from supex_driver.agent.errors import GuideError

GUIDE_DIR = prompts.resolve_guide_dir()


def test_guide_dir_resolves_in_repo_checkout() -> None:
    assert GUIDE_DIR is not None
    assert (GUIDE_DIR / "README.md").is_file()
    assert (GUIDE_DIR / "mcp.md").is_file()


def test_guide_available_true_in_checkout() -> None:
    assert prompts.guide_available() is True


def test_load_guide_file_returns_markdown() -> None:
    name, text = prompts.load_guide_file("README.md")
    assert name == "README.md"
    assert "SketchUp" in text
    assert len(text) > 50


def test_load_guide_file_default_set_all_loadable() -> None:
    for name in prompts.GUIDE_FILES:
        _name, text = prompts.load_guide_file(name)
        assert len(text.strip()) > 0


@pytest.mark.parametrize(
    "bad",
    ["README", "notes.txt", "sub/README.md", "../README.md", "README.md/x.md"],
)
def test_load_guide_file_rejects_non_guide_names(bad: str) -> None:
    with pytest.raises(GuideError):
        prompts.load_guide_file(bad)


def test_build_system_prompt_includes_workspace_and_sections(tmp_path) -> None:
    prompt = prompts.build_system_prompt(tmp_path)
    assert str(tmp_path) in prompt
    assert "supex-chat" in prompt
    for name in prompts.GUIDE_FILES:
        assert f"# Guide section: {name}" in prompt


def test_build_system_prompt_respects_files_subset(tmp_path) -> None:
    prompt = prompts.build_system_prompt(tmp_path, files=["mcp.md"])
    assert "# Guide section: mcp.md" in prompt
    assert "# Guide section: ruby.md" not in prompt


def test_build_system_prompt_vision_disabled_says_cannot_view(tmp_path) -> None:
    prompt = prompts.build_system_prompt(tmp_path, vision=False)
    assert "cannot view image files" in prompt
    assert "Image content can accompany" not in prompt


def test_build_system_prompt_vision_enabled_mentions_images(tmp_path) -> None:
    prompt = prompts.build_system_prompt(tmp_path, vision=True)
    assert "Image content can accompany" in prompt
    assert "cannot view image files" not in prompt


def test_bundled_fallback_missing_raises_helpful_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(prompts, "_repo_guide_dir", lambda: None)
    with pytest.raises(GuideError, match="no repo checkout"):
        prompts.load_guide_file("README.md")


def test_bundled_fallback_reads_package_data(monkeypatch) -> None:
    monkeypatch.setattr(prompts, "_repo_guide_dir", lambda: None)

    class FakeFile:
        def read_text(self, encoding: str = "utf-8") -> str:
            return "# bundled guide"

    class FakeFiles:
        def joinpath(self, name: str) -> FakeFile:
            assert name == "README.md"
            return FakeFile()

    class FakeResources:
        def files(self, _package: str) -> FakeFiles:
            return FakeFiles()

    monkeypatch.setattr(prompts, "resources", FakeResources())
    name, text = prompts.load_guide_file("README.md")
    assert name == "README.md"
    assert text == "# bundled guide"
    assert prompts.guide_available() is True

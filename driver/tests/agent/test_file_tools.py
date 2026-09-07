"""Tests for the agent workspace file tools (file_tools.py)."""

import json
import os

import pytest

from supex_driver.agent import file_tools
from supex_driver.agent.errors import FileToolError, PathNotAllowedError
from supex_driver.agent.file_tools import FileTools


def _payload(text):
    return json.loads(text)


def _write(path, content):
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def ft(workspace):
    return FileTools(workspace=workspace)


class TestPathPolicy:
    def test_relative_resolves_under_workspace(self, ft, workspace):
        _write(workspace / "a.rb", "puts 1")
        assert ft.resolve("a.rb") == (workspace / "a.rb").resolve()

    def test_absolute_path_in_workspace_ok(self, ft, workspace):
        _write(workspace / "a.rb", "puts 1")
        assert ft.resolve(workspace / "a.rb") == (workspace / "a.rb").resolve()

    def test_dotdot_escape_denied(self, ft, tmp_path):
        _write(tmp_path / "secret.txt", "s")
        with pytest.raises(PathNotAllowedError):
            ft.resolve("../../secret.txt")

    def test_absolute_escape_denied(self, ft, tmp_path):
        _write(tmp_path / "secret.txt", "s")
        with pytest.raises(PathNotAllowedError):
            ft.resolve(str(tmp_path / "secret.txt"))

    def test_symlink_escape_denied(self, ft, workspace, tmp_path):
        if not hasattr(os, "symlink"):
            pytest.skip("no symlink support")
        _write(tmp_path / "secret.txt", "s")
        (workspace / "link.txt").symlink_to(tmp_path / "secret.txt")
        with pytest.raises(PathNotAllowedError):
            ft.resolve("link.txt")

    def test_allowed_roots_extends_workspace(self, workspace, tmp_path):
        extra = tmp_path / "extra"
        extra.mkdir()
        _write(extra / "x.rb", "1")
        ft = FileTools(workspace=workspace, allowed_roots=str(extra))
        assert ft.resolve(str(extra / "x.rb")) == (extra / "x.rb").resolve()
        with pytest.raises(PathNotAllowedError):
            ft.resolve(str(tmp_path / "secret.txt"))

    def test_star_disables_containment(self, workspace, tmp_path):
        _write(tmp_path / "secret.txt", "s")
        ft = FileTools(workspace=workspace, allowed_roots="*")
        assert (
            ft.resolve(str(tmp_path / "secret.txt"))
            == (tmp_path / "secret.txt").resolve()
        )

    def test_os_pathsep_splits_allowed_roots(self, workspace, tmp_path):
        extra_a = tmp_path / "a"
        extra_b = tmp_path / "b"
        extra_a.mkdir()
        extra_b.mkdir()
        _write(extra_a / "a.txt", "1")
        _write(extra_b / "b.txt", "2")
        ft = FileTools(
            workspace=workspace, allowed_roots=f"{extra_a}{os.pathsep}{extra_b}"
        )
        assert ft.resolve(str(extra_a / "a.txt")) == (extra_a / "a.txt").resolve()
        assert ft.resolve(str(extra_b / "b.txt")) == (extra_b / "b.txt").resolve()


class TestReadWriteEdit:
    async def test_write_creates_parent_dirs(self, ft, workspace):
        out = _payload(
            await ft.call("write_file", {"path": "sub/dir/f.rb", "content": "x = 1"})
        )
        assert out["ok"] is True
        assert (workspace / "sub/dir/f.rb").read_text() == "x = 1"

    async def test_read_returns_utf8_text(self, ft, workspace):
        _write(workspace / "t.rb", "hello world")
        out = await ft.call("read_file", {"path": "t.rb"})
        assert out == "hello world"

    async def test_read_binary_flagged(self, ft, workspace):
        _write(workspace / "bin.dat", b"\xff\xfe\x00binary")
        out = _payload(await ft.call("read_file", {"path": "bin.dat"}))
        assert out["ok"] is True
        assert out["binary"] is True
        assert "media_type" in out
        assert out["hint"]

    async def test_read_beyond_cap_rejected(self, ft, workspace):
        big = b"a" * (file_tools._READ_CAP_BYTES + 1)
        _write(workspace / "big.bin", big)
        out = _payload(await ft.call("read_file", {"path": "big.bin"}))
        assert out["ok"] is False

    async def test_edit_replace_once(self, ft, workspace):
        _write(workspace / "e.txt", "foo foo foo")
        out = _payload(
            await ft.call(
                "edit_file", {"path": "e.txt", "old_text": "foo", "new_text": "bar"}
            )
        )
        assert out["ok"] is True
        assert out["replaced"] == 1
        assert (workspace / "e.txt").read_text() == "bar foo foo"

    async def test_edit_replace_all(self, ft, workspace):
        _write(workspace / "e.txt", "foo foo foo")
        out = _payload(
            await ft.call(
                "edit_file",
                {
                    "path": "e.txt",
                    "old_text": "foo",
                    "new_text": "bar",
                    "replace_all": True,
                },
            )
        )
        assert out["replaced"] == 3
        assert (workspace / "e.txt").read_text() == "bar bar bar"

    async def test_edit_missing_old_text(self, ft, workspace):
        _write(workspace / "e.txt", "abc")
        out = _payload(
            await ft.call(
                "edit_file", {"path": "e.txt", "old_text": "zzz", "new_text": "x"}
            )
        )
        assert out["ok"] is False
        assert "not found" in out["error"]

    async def test_edit_non_utf8_rejected(self, ft, workspace):
        _write(workspace / "e.bin", b"\xff\xfe")
        out = _payload(
            await ft.call(
                "edit_file", {"path": "e.bin", "old_text": "a", "new_text": "b"}
            )
        )
        assert out["ok"] is False

    async def test_list_dir(self, ft, workspace):
        _write(workspace / "z.txt", "1")
        _write(workspace / "a.txt", "2")
        (workspace / "sub").mkdir()
        out = _payload(await ft.call("list_dir", {}))
        assert out["ok"] is True
        names = [e["name"] for e in out["entries"]]
        assert names == ["a.txt", "sub", "z.txt"]
        kinds = {e["name"]: e["type"] for e in out["entries"]}
        assert kinds["sub"] == "dir"
        assert kinds["a.txt"] == "file"


class TestDelete:
    async def test_delete_disabled_by_default(self, ft, workspace):
        _write(workspace / "d.txt", "x")
        out = _payload(await ft.call("delete_file", {"path": "d.txt"}))
        assert out["ok"] is False
        assert "disabled" in out["error"]
        assert (workspace / "d.txt").exists()

    async def test_delete_works_when_enabled(self, workspace):
        _write(workspace / "d.txt", "x")
        ft = FileTools(workspace=workspace, allow_delete=True)
        out = _payload(await ft.call("delete_file", {"path": "d.txt"}))
        assert out["ok"] is True
        assert not (workspace / "d.txt").exists()

    async def test_delete_refuses_workspace_root(self, workspace):
        ft = FileTools(workspace=workspace, allow_delete=True)
        with pytest.raises(PathNotAllowedError):
            await ft.call("delete_file", {"path": "."})

    async def test_delete_refuses_directory(self, workspace):
        (workspace / "sub").mkdir()
        ft = FileTools(workspace=workspace, allow_delete=True)
        with pytest.raises(FileToolError):
            await ft.call("delete_file", {"path": "sub"})


class TestSchemas:
    def test_schema_names(self, ft):
        names = [s.name for s in ft.schemas()]
        assert names == [
            "read_file",
            "write_file",
            "edit_file",
            "list_dir",
            "delete_file",
        ]

    def test_write_schema_has_content(self, ft):
        schema = next(s for s in ft.schemas() if s.name == "write_file")
        props = schema.input_schema.get("properties", {})
        assert "path" in props
        assert "content" in props

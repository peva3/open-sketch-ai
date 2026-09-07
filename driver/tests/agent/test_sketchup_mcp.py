"""Tests for the Supex MCP backend client (sketchup_mcp.py)."""

import sys

import pytest

from supex_driver.agent import sketchup_mcp
from supex_driver.agent.errors import BackendConnectionError, BackendToolError
from supex_driver.agent.sketchup_mcp import SketchUpMCP, resolve_backend_command


def _backend_command():
    return [sys.executable, "-m", "supex_driver"]


class TestResolveBackendCommand:
    def test_module_fallback_when_no_console_script(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sketchup_mcp.shutil, "which", lambda _name: None)
        cmd = resolve_backend_command()
        assert cmd[0] == sys.executable
        assert cmd[1:] == ["-m", "supex_driver"]

    def test_console_script_preferred(self, monkeypatch, tmp_path):
        fake = tmp_path / "supex-mcp"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sketchup_mcp.shutil, "which", lambda _name: str(fake))
        cmd = resolve_backend_command()
        assert cmd == [str(fake)]

    def test_frozen_uses_sibling_exe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        fake = tmp_path / "supex-chat.exe"
        monkeypatch.setattr(sketchup_mcp.sys, "executable", str(fake))
        cmd = resolve_backend_command()
        expected = tmp_path / (
            "supex-mcp.exe" if __import__("os").name == "nt" else "supex-mcp"
        )
        assert cmd == [str(expected)]


class TestSketchUpMCPUnit:
    def test_env_includes_workspace(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path)
        env = mcp_client._env
        assert env["SUPEX_WORKSPACE"] == str(tmp_path.resolve())
        for key, value in mcp_client._env.items():
            if not key.startswith("SUPEX"):
                raise AssertionError(f"non-SUPEX env leaked: {key}={value!r}")

    def test_env_auth_token_passthrough(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path, auth_token="secret-token")
        assert mcp_client._env.get("SUPEX_AUTH_TOKEN") == "secret-token"

    def test_env_allowed_roots_passthrough(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path, allowed_roots="/tmp:/var/tmp")
        assert mcp_client._env.get("SUPEX_ALLOWED_ROOTS") == "/tmp:/var/tmp"

    def test_command_property_returns_copy(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path)
        cmd = mcp_client.command
        cmd.append("extra")
        assert "extra" not in mcp_client.command

    def test_tools_property_empty_before_connect(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path)
        assert mcp_client.tools == []


class TestBackendSpawn:
    @pytest.mark.asyncio
    async def test_list_tools_returns_27(self, tmp_path):
        async with SketchUpMCP(
            workspace=tmp_path, command=_backend_command()
        ) as mcp_client:
            tools = await mcp_client.list_tools()
            names = {t.name for t in tools}
            assert len(tools) == 27
            assert {
                "eval_ruby",
                "eval_ruby_file",
                "check_status",
                "get_model_info",
            } <= names

    @pytest.mark.asyncio
    async def test_call_tool_returns_text(self, tmp_path):
        async with SketchUpMCP(
            workspace=tmp_path, command=_backend_command()
        ) as mcp_client:
            text = await mcp_client.call_tool("check_status")
            assert isinstance(text, str)
            assert len(text) > 0

    @pytest.mark.asyncio
    async def test_agent_name_reaches_backend(self, tmp_path):
        async with SketchUpMCP(
            workspace=tmp_path,
            command=_backend_command(),
            agent_name="unit-test-agent",
        ) as mcp_client:
            await mcp_client.list_tools()
            assert mcp_client._agent_name == "unit-test-agent"

    @pytest.mark.asyncio
    async def test_missing_binary_raises_connection_error(self, tmp_path):
        cmd = [str(tmp_path / "does-not-exist"), "serve"]
        mcp_client = SketchUpMCP(workspace=tmp_path, command=cmd)
        with pytest.raises(BackendConnectionError):
            await mcp_client.connect()

    @pytest.mark.asyncio
    async def test_context_manager_closes(self, tmp_path):
        mcp_client = SketchUpMCP(workspace=tmp_path, command=_backend_command())
        async with mcp_client:
            assert mcp_client._session is not None
        assert mcp_client._session is None


class TestToolErrors:
    @pytest.mark.asyncio
    async def test_unknown_tool_is_backend_error(self, tmp_path):
        async with SketchUpMCP(
            workspace=tmp_path, command=_backend_command()
        ) as mcp_client:
            with pytest.raises(BackendToolError):
                await mcp_client.call_tool("no_such_tool", {})

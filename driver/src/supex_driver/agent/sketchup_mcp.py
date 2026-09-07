"""SketchUp backend for the agent: a thin MCP *client* over stdio.

The agent drives SketchUp the same way Claude Code does: by spawning the
Supex MCP server (``supex_driver``'s stdio entry point) as a subprocess and
speaking MCP to it. This module owns that client session, maps MCP tools to
the agent's internal :class:`ToolSchema`, and executes ``tools/call``.

The backend subprocess is spawned with an explicit, distribution-aware
command -- never the repo's ``./mcp`` bash wrapper (see T-2.1/T-7.2).
SketchUp does not need to be running: the server starts headless and only
touches the SketchUp bridge when a tool that needs it is called.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.types import Implementation, TextContent

from supex_driver.agent.errors import (
    BackendConnectionError,
    BackendError,
    BackendToolError,
)
from supex_driver.agent.providers.base import ToolSchema

AGENT_NAME = "supex-chat"
_PACKAGE = "supex-driver"


def agent_version() -> str:
    """Return the installed supex-driver version, best effort."""
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def resolve_backend_command() -> list[str]:
    """Return the command used to spawn the MCP backend.

    Distribution-aware (T-2.1 / T-7.2): a frozen PyInstaller build spawns a
    sibling ``supex-mcp`` executable; an installed package prefers the
    ``supex-mcp`` console script on PATH; a source checkout falls back to
    ``python -m supex_driver`` (runs ``supex_driver.__main__``, which starts
    the stdio MCP server). The repo ``./mcp`` bash wrapper is never used.
    """
    if getattr(sys, "frozen", False):
        exe = "supex-mcp.exe" if os.name == "nt" else "supex-mcp"
        sibling = Path(sys.executable).resolve().parent / exe
        return [str(sibling)]
    script = shutil.which("supex-mcp")
    if script:
        return [script]
    return [sys.executable, "-m", "supex_driver"]


def _text_of(result: object) -> str:
    """Flatten MCP tool-result content blocks into a single string."""
    blocks = getattr(result, "content", None)
    if not blocks:
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif block is not None:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts)


class SketchUpMCP:
    """Client session to a spawned Supex MCP server subprocess.

    Lazily spawns and connects on first use (or via :meth:`connect`).
    Holds one stdio MCP session; :meth:`call_tool` transparently reconnects
    once if the subprocess died between calls.
    """

    # Attribute annotations: these are created lazily and reassigned on
    # reconnect, so the stdio/session context managers are typed loosely.
    _session: ClientSession | None
    _session_cm: Any
    _stdio: Any
    _read: Any
    _write: Any
    _tools: list[ToolSchema] | None

    def __init__(
        self,
        *,
        workspace: str | Path,
        command: Sequence[str] | None = None,
        agent_name: str = AGENT_NAME,
        auth_token: str | None = None,
        allowed_roots: str | None = None,
        read_timeout_seconds: float | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._agent_name = agent_name
        self._read_timeout = read_timeout_seconds
        self._command = (
            list(command) if command is not None else resolve_backend_command()
        )
        self._session: ClientSession | None = None
        self._stdio = None
        self._read = None
        self._write = None
        self._tools: list[ToolSchema] | None = None
        self._env = self._build_env(auth_token, allowed_roots)

    def _build_env(
        self, auth_token: str | None, allowed_roots: str | None
    ) -> dict[str, str]:
        """Environment for the spawned server: our SUPEX_* + explicit core."""
        env = {k: v for k, v in os.environ.items() if k.startswith("SUPEX")}
        env["SUPEX_WORKSPACE"] = str(self._workspace)
        if auth_token:
            env["SUPEX_AUTH_TOKEN"] = auth_token
        if allowed_roots:
            env["SUPEX_ALLOWED_ROOTS"] = allowed_roots
        return env

    @property
    def command(self) -> list[str]:
        """The resolved spawn command (useful for --check output)."""
        return list(self._command)

    @property
    def tools(self) -> list[ToolSchema]:
        """The backend's tool schemas (None-safe before connect)."""
        if self._tools is None:
            return []
        return list(self._tools)

    async def connect(self) -> None:
        """Spawn the backend subprocess and initialize the MCP session."""
        if self._session is not None:
            return
        if not self._command or not self._command[0]:
            raise BackendConnectionError("no MCP backend command configured")
        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=self._env,
            cwd=str(self._workspace),
        )
        try:
            self._stdio = stdio_client(params)
            self._read, self._write = await self._stdio.__aenter__()
            self._session_cm = ClientSession(
                self._read,
                self._write,
                client_info=Implementation(
                    name=self._agent_name, version=agent_version()
                ),
            )
            self._session = await self._session_cm.__aenter__()
            await self._session.initialize()
            self._tools = await self._refresh_tools()
        except BackendError:
            await self._teardown()
            raise
        except Exception as exc:  # spawn / protocol failure
            await self._teardown()
            raise BackendConnectionError(
                f"failed to start MCP backend {self._command}: {exc}"
            ) from exc

    async def _refresh_tools(self) -> list[ToolSchema]:
        if self._session is None:
            raise BackendConnectionError("MCP backend not connected")
        result = await self._session.list_tools()
        schemas: list[ToolSchema] = []
        for tool in result.tools:
            schemas.append(
                ToolSchema(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema or {}),
                )
            )
        return schemas

    async def __aenter__(self) -> SketchUpMCP:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _teardown(self) -> None:
        for cm in (getattr(self, "_session_cm", None), self._stdio):
            if cm is not None:
                with contextlib.suppress(Exception):
                    await cm.__aexit__(None, None, None)
        self._session = None
        self._session_cm = None
        self._stdio = None
        self._read = None
        self._write = None

    async def aclose(self) -> None:
        """Close the backend subprocess and release the session."""
        await self._teardown()
        self._tools = None

    async def list_tools(self) -> list[ToolSchema]:
        """Return (and cache) the backend's tools as internal schemas."""
        if self._tools is None:
            await self.connect()
        assert self._tools is not None
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """Execute one backend tool and return its text result.

        Tool failures that the server reports (``is_error``) raise
        :class:`BackendToolError`; transport-level failures reconnect once
        and retry before surfacing a :class:`BackendConnectionError`.
        """
        if self._session is None:
            await self.connect()
        try:
            session = self._session
            assert session is not None
            result = await session.call_tool(
                name, arguments or {}, read_timeout_seconds=self._read_timeout
            )
        except Exception:  # transport-level; reconnect once
            await self._teardown()
            await self.connect()
            try:
                session = self._session
                assert session is not None
                result = await session.call_tool(
                    name,
                    arguments or {},
                    read_timeout_seconds=self._read_timeout,
                )
            except Exception as retry_exc:
                raise BackendConnectionError(
                    f"MCP backend call {name!r} failed after reconnect: {retry_exc}"
                ) from retry_exc
        if result.is_error:
            raise BackendToolError(name, _text_of(result) or "remote tool error")
        return _text_of(result)

"""Public ``Agent`` facade for supex-chat.

Wires configuration, a provider, the SketchUp MCP backend, workspace file
tools, the system prompt, and the agentic loop into one object exposing
:meth:`run_turn` — the single seam the chat CLI (and future UIs) call.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import (
    BackendToolError,
    FileToolError,
    PathNotAllowedError,
)
from supex_driver.agent.file_tools import FileTools
from supex_driver.agent.loop import AgentLoop, TurnResult
from supex_driver.agent.prompts import build_system_prompt
from supex_driver.agent.providers import build_provider
from supex_driver.agent.providers.base import (
    ChatProvider,
    ImagePart,
    Message,
    ProviderEvent,
    ToolSchema,
)
from supex_driver.agent.sketchup_mcp import SketchUpMCP


class Agent:
    """A single supex-chat session: provider + SketchUp backend + loop."""

    def __init__(
        self,
        *,
        config: ProviderConfig,
        provider: ChatProvider | None = None,
        workspace: str | Path | None = None,
        allow_delete: bool = False,
        command: Sequence[str] | None = None,
        read_timeout_seconds: float | None = None,
        on_event: Callable[[ProviderEvent], None] | None = None,
    ) -> None:
        self._config = config
        self._files = FileTools(workspace=workspace, allow_delete=allow_delete)
        self._sketchup = SketchUpMCP(
            workspace=self._files.workspace,
            command=command,
            read_timeout_seconds=read_timeout_seconds,
        )
        self._provider = provider or build_provider(config)
        self._on_event = on_event
        self._loop: AgentLoop | None = None
        self._tools: list[ToolSchema] | None = None
        self._sketchup_names: set[str] = set()
        self._file_names = set(FileTools.TOOL_NAMES)

    @property
    def provider(self) -> ChatProvider:
        return self._provider

    @property
    def files(self) -> FileTools:
        return self._files

    @property
    def history(self) -> list[Message]:
        """Neutral conversation history (empty before the first turn)."""
        if self._loop is None:
            return []
        return self._loop.history

    async def _ensure_loop(self) -> AgentLoop:
        if self._loop is None:
            sketchup_tools = await self._sketchup.list_tools()
            self._sketchup_names = {tool.name for tool in sketchup_tools}
            self._tools = list(sketchup_tools) + self._files.schemas()
            system = build_system_prompt(
                self._files.workspace, vision=self._config.vision
            )
            self._loop = AgentLoop(
                provider=self._provider,
                system=system,
                tools=self._tools,
                execute=self._execute_tool,
                max_iterations=self._config.max_iterations,
                on_event=self._on_event,
            )
        return self._loop

    async def tools(self) -> list[ToolSchema]:
        """Expose the combined tool schema set (connects the backend)."""
        loop = await self._ensure_loop()
        return list(loop.tools)

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Route one tool call to the file tools or the SketchUp backend."""
        if name in self._file_names:
            try:
                return await self._files.call(name, arguments)
            except (PathNotAllowedError, FileToolError) as exc:
                return json.dumps({"ok": False, "error": str(exc)})
        if name in self._sketchup_names:
            try:
                return await self._sketchup.call_tool(name, arguments)
            except BackendToolError as exc:
                return (
                    f"tool {name!r} failed remotely; correct your approach and "
                    f"retry, or ask the user to check SketchUp. {exc}"
                )
        return json.dumps({"ok": False, "error": f"unknown tool {name!r}"})

    async def run_turn(
        self,
        text: str,
        *,
        images: Sequence[ImagePart] | None = None,
    ) -> TurnResult:
        """Run one user turn to completion (streaming via the event handler)."""
        loop = await self._ensure_loop()
        return await loop.run_turn(text, images=images)

    def reset_conversation(self) -> None:
        """Forget history; a fresh loop starts on the next :meth:`run_turn`."""
        self._loop = None

    async def aclose(self) -> None:
        """Release the provider HTTP client and the MCP backend process."""
        await self._provider.aclose()
        await self._sketchup.aclose()

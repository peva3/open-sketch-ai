"""Public ``Agent`` facade for supex-chat.

Wires configuration, a provider, the SketchUp MCP backend, workspace file
tools, the system prompt, and the agentic loop into one object exposing
:meth:`run_turn` — the single seam the chat CLI (and future UIs) call.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import (
    BackendError,
    BackendToolError,
    FileToolError,
    PathNotAllowedError,
)
from supex_driver.agent.file_tools import FileTools
from supex_driver.agent.loop import AgentLoop, ToolResultHandler, TurnResult
from supex_driver.agent.prompts import build_system_prompt
from supex_driver.agent.providers import build_provider
from supex_driver.agent.providers.base import (
    ChatProvider,
    ImagePart,
    Message,
    ProviderEvent,
    ToolSchema,
    Usage,
)
from supex_driver.agent.sketchup_mcp import SketchUpMCP

# Tools whose result text references viewport screenshots on disk. When vision
# is enabled the model's own screenshot is read back and attached to the next
# turn so it can visually verify what it just did.
_SCREENSHOT_TOOL_NAMES = frozenset(
    {"take_screenshot", "take_batch_screenshots", "vcad_viewer_screenshot"}
)
_MAX_VISUAL_IMAGES = 8
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_IMAGE_MEDIA_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_PATH_RE = re.compile(r"[\w./\\-]+\.(?:png|jpe?g|webp)", re.IGNORECASE)


def _image_path_tokens(text: str) -> list[str]:
    """Return candidate image file paths mentioned in a tool result.

    Tolerates bare paths, JSON objects carrying ``path``/``screenshot``/
    ``file`` keys, and arrays of paths. JSON string values are preferred;
    otherwise any ``*.png|jpg|jpeg|webp`` token in the text is a candidate.
    """
    candidates: list[str] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError, TypeError:
        payload = None
    if isinstance(payload, (dict, list)):
        stack: list[Any] = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for _key, value in node.items():
                    if isinstance(value, str) and _PATH_RE.fullmatch(value):
                        candidates.append(value)
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    if not candidates:
        candidates = list(_PATH_RE.findall(text))
    return candidates


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
        on_tool_result: ToolResultHandler | None = None,
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
        self._on_tool_result = on_tool_result
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

    @property
    def total_usage(self) -> Usage | None:
        """Cumulative provider-reported token usage, or None."""
        if self._loop is None:
            return None
        return self._loop.total_usage

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
                collect_visual=self._visual_images,
                on_tool_result=self._on_tool_result,
            )
        return self._loop

    @property
    def config(self) -> ProviderConfig:
        """The resolved provider configuration for this session."""
        return self._config

    async def tools(self) -> list[ToolSchema]:
        """Expose the combined tool schema set (connects the backend)."""
        loop = await self._ensure_loop()
        return list(loop.tools)

    async def backend_status(self) -> dict[str, Any]:
        """Ping the SketchUp backend and report its health.

        Connects the loop (listing tools) then runs the ``check_status`` MCP
        tool against the runtime. Returns the tool count plus the raw status
        text so callers (``--check``, ``/status``) can render it. Never raises
        for a disconnected SketchUp -- the runtime reports that state in the
        status text; only a broken MCP backend raises :class:`BackendError`.
        """
        tools = await self.tools()
        try:
            status_text = await self._sketchup.call_tool("check_status", {})
        except BackendError as exc:
            status_text = f"backend error: {exc}"
        return {"tools": len(tools), "check_status": status_text}

    def _visual_images(
        self, name: str, arguments: dict[str, Any], result: str
    ) -> list[ImagePart]:
        """Load screenshots the model requested back as image content.

        Vision mode only: when the tool is a screenshot tool its result text
        names an image file on disk; that file is read (containment-checked
        against the workspace) and returned as base64 :class:`ImagePart`
        objects so the provider can show the model what it just produced.
        Returns an empty list when vision is off, the tool produced no image,
        or nothing on disk could be resolved, so non-vision sessions keep
        seeing only the on-disk path text.
        """
        if not self._config.vision or name not in _SCREENSHOT_TOOL_NAMES:
            return []
        parts: list[ImagePart] = []
        seen: set[str] = set()
        for raw in _image_path_tokens(result):
            if len(parts) >= _MAX_VISUAL_IMAGES:
                break
            try:
                path = self._files.resolve(raw)
            except PathNotAllowedError:
                continue
            key = str(path)
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            try:
                size = path.stat().st_size
                if size <= 0 or size > _MAX_IMAGE_BYTES:
                    continue
                data = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
            media = _IMAGE_MEDIA_BY_SUFFIX.get(path.suffix.lower())
            if media is None:
                continue
            parts.append(ImagePart(media_type=media, data=data))
        return parts

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

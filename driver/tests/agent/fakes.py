"""Scripted fakes shared by the agent-loop and agent-facade tests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from supex_driver.agent.providers.base import (
    Done,
    Message,
    ProviderEvent,
    ToolSchema,
)


class ScriptedProvider:
    """Fake provider replaying pre-scripted event turns per stream call.

    Each element of ``turns`` is the full event sequence for one model
    turn; turns are consumed in order. Snapshots of every (messages,
    tools, system) triple are recorded on ``calls`` for assertions.
    """

    def __init__(
        self,
        turns: Iterable[Iterable[ProviderEvent]] | None = None,
    ) -> None:
        self._turns = [list(turn) for turn in turns] if turns else []
        self.calls: list[tuple[list[Message], list[ToolSchema], str | None]] = []
        self.closed = False

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
    ):
        self.calls.append((list(messages), list(tools or ()), system))
        turn = list(self._turns.pop(0)) if self._turns else [Done("end_turn")]
        for event in turn:
            yield event

    async def aclose(self) -> None:
        self.closed = True


class FakeBackend:
    """Stand-in for the SketchUp MCP backend (no subprocess spawned)."""

    def __init__(self, tools: Sequence[ToolSchema] | None = None) -> None:
        self._tools = list(tools or [])
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def add_tool(self, tool: ToolSchema) -> None:
        self._tools.append(tool)

    async def list_tools(self) -> list[ToolSchema]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "explode":
            from supex_driver.agent.errors import BackendToolError

            raise BackendToolError(name, "remote boom")
        return '{"ok": true}'

    async def aclose(self) -> None:
        self.closed = True

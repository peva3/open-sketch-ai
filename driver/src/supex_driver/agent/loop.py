"""Provider-neutral agentic loop for supex-chat.

The loop keeps conversation history as provider-neutral :class:`Message`
objects and hands them to whichever provider is configured; each provider
already knows how to translate them to its own wire dialect (OpenAI tool
messages vs. Anthropic ``tool_use``/``tool_result`` blocks). After each
model turn any requested tool calls are executed through an injected
``execute`` callback and their results appended to history, then the loop
iterates until the model finishes or ``max_iterations`` is reached.

Only the model/provider stream is touched here; the SketchUp MCP backend,
workspace file tools, and any future backend all arrive through the same
``execute(name, arguments) -> text`` seam, so the loop is testable against a
scripted fake provider and fake executor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from supex_driver.agent.providers.base import (
    ChatProvider,
    Done,
    ImagePart,
    Message,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSchema,
    Usage,
)

ExecuteTool = Callable[[str, dict[str, Any]], Awaitable[str]]
StopReason = Literal["end_turn", "tool_limit"]
_ToolEventHandler = Callable[[ProviderEvent], None]
# Tool name/arguments/result-text -> images the model should see next turn.
_VisualExtractor = Callable[[str, dict[str, Any], str], Sequence[ImagePart]]
# Tool id/name/arguments/result-text/images fired after each executed tool
# call, so every consumer (terminal CLI, chat server, UI) renders tool
# activity the same way.
ToolResultHandler = Callable[
    [str, str, dict[str, Any], str, Sequence[ImagePart]], None
]

_TOOL_RESULT_CAP = 20_000
_VISUAL_NOTE = (
    "The image(s) above were captured by the tool call you just made. "
    "Use them to visually verify the current SketchUp state before "
    "finishing your reply."
)


def truncate_result(text: str, cap: int = _TOOL_RESULT_CAP) -> str:
    """Cap an oversized tool result so history stays model-friendly."""
    if len(text) <= cap:
        return text
    return (
        f"{text[:cap]}\n\n...[tool result truncated; original {len(text)} "
        "characters]..."
    )


@dataclass
class TurnResult:
    """Outcome of one :meth:`AgentLoop.run_turn` call."""

    text: str
    iterations: int
    stopped: StopReason
    tool_calls: int
    usage: Usage | None = None


class AgentLoop:
    """One agent session: shared history plus the run-until-finished loop."""

    def __init__(
        self,
        *,
        provider: ChatProvider,
        system: str,
        tools: Sequence[ToolSchema],
        execute: ExecuteTool,
        max_iterations: int = 10,
        max_result_chars: int = _TOOL_RESULT_CAP,
        on_event: _ToolEventHandler | None = None,
        collect_visual: _VisualExtractor | None = None,
        on_tool_result: ToolResultHandler | None = None,
    ) -> None:
        self.provider = provider
        self.system = system
        self.tools = tuple(tools)
        self.execute = execute
        self.max_iterations = max_iterations
        self.max_result_chars = max_result_chars
        self.on_event = on_event
        self.collect_visual = collect_visual
        self.on_tool_result = on_tool_result
        # Anthropic can embed image blocks inside tool_result content; the
        # OpenAI wire format only allows string tool content, so its loop
        # surfaces captured images via a trailing user message instead.
        self._images_in_tool_message = (
            getattr(provider, "dialect", "openai") == "anthropic"
        )
        self.history: list[Message] = []

    async def _stream_once(self) -> tuple[str, list[ToolCallEvent], Usage | None]:
        """Run one model turn; return (text, tool calls, usage)."""
        text_parts: list[str] = []
        calls: list[ToolCallEvent] = []
        usage: Usage | None = None
        async for event in self.provider.stream(
            self.history, tools=self.tools, system=self.system
        ):
            if self.on_event is not None:
                self.on_event(event)
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallEvent):
                calls.append(event)
            elif isinstance(event, Done):
                usage = event.usage or usage
        return "".join(text_parts), calls, usage

    @staticmethod
    def _drop_dangling_tool_calls(history: list[Message]) -> None:
        """Remove a trailing assistant message whose tool calls never ran."""
        if history and history[-1].role == "assistant" and history[-1].tool_calls:
            history.pop()

    async def run_turn(
        self,
        text: str,
        *,
        images: Sequence[ImagePart] | None = None,
    ) -> TurnResult:
        """Run the loop to completion for one user message.

        Appends the user message, then streams model turns, executing any
        requested tools and feeding results back until the model stops
        requesting tools or ``max_iterations`` turns are exhausted.
        """
        self.history.append(
            Message(role="user", content=text, images=list(images or []))
        )
        turn_text: list[str] = []
        executed = 0
        iterations = 0
        usage: Usage | None = None

        while True:
            iterations += 1
            if iterations > self.max_iterations:
                self._drop_dangling_tool_calls(self.history)
                return TurnResult(
                    text="\n\n".join(turn_text).strip(),
                    iterations=iterations - 1,
                    stopped="tool_limit",
                    tool_calls=executed,
                    usage=usage,
                )
            parts, calls, turn_usage = await self._stream_once()
            usage = turn_usage or usage
            if parts:
                turn_text.append(parts)
            if not calls:
                if parts:
                    self.history.append(Message(role="assistant", content=parts))
                return TurnResult(
                    text="\n\n".join(turn_text).strip(),
                    iterations=iterations,
                    stopped="end_turn",
                    tool_calls=executed,
                    usage=usage,
                )
            self.history.append(
                Message(
                    role="assistant",
                    content=parts,
                    tool_calls=[
                        ToolCall(id=call.id, name=call.name, arguments=call.arguments)
                        for call in calls
                    ],
                )
            )
            # Captured screenshots the model should see next turn. Anthropic
            # carries them on the tool message itself; OpenAI needs a trailing
            # user message because its tool content is string-only.
            visual_parts: list[ImagePart] = []
            for call in calls:
                arguments = parse_arguments(call.arguments)
                result = await self.execute(call.name, arguments)
                executed += 1
                truncated = truncate_result(result, self.max_result_chars)
                images = (
                    list(self.collect_visual(call.name, arguments, result))
                    if self.collect_visual is not None
                    else []
                )
                if self._images_in_tool_message:
                    self.history.append(
                        Message(
                            role="tool",
                            tool_call_id=call.id,
                            content=truncated,
                            images=images,
                        )
                    )
                else:
                    self.history.append(
                        Message(
                            role="tool",
                            tool_call_id=call.id,
                            content=truncated,
                        )
                    )
                    visual_parts.extend(images)
                if self.on_tool_result is not None:
                    self.on_tool_result(call.id, call.name, arguments, truncated, images)
            if visual_parts:
                self.history.append(
                    Message(role="user", content=_VISUAL_NOTE, images=visual_parts)
                )


def parse_arguments(arguments: str) -> dict[str, Any]:
    """Parse tool-call arguments defensively; tolerate a missing/empty map."""
    from supex_driver.agent.providers.base import parse_tool_arguments

    return parse_tool_arguments(arguments)

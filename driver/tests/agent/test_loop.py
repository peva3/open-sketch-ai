"""Tests for the provider-neutral agentic loop."""

from __future__ import annotations

import pytest

from supex_driver.agent.errors import ProviderProtocolError
from supex_driver.agent.loop import (
    _VISUAL_NOTE,
    AgentLoop,
    TurnResult,
    parse_arguments,
    truncate_result,
)
from supex_driver.agent.providers.anthropic import _encode_messages as _anthropic_encode
from supex_driver.agent.providers.base import (
    Done,
    ImagePart,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSchema,
    Usage,
)
from supex_driver.agent.providers.openai import _encode_messages as _openai_encode
from tests.agent.fakes import ScriptedProvider

EVAL = ToolSchema(name="eval_ruby", description="eval", input_schema={"type": "object"})


def _single_turn(text: str, usage: Usage | None = None) -> list[object]:
    return [TextDelta(text), Done("end_turn", usage)]


def _make_loop(provider: ScriptedProvider, execute, **kwargs) -> AgentLoop:
    return AgentLoop(
        provider=provider,
        system="sys",
        tools=[EVAL],
        execute=execute,
        **kwargs,
    )


async def _recorder():
    calls = []

    async def execute(name: str, arguments: dict) -> str:
        calls.append((name, arguments))
        return '{"ok": true}'

    return calls, execute


def test_truncate_result_short_passthrough() -> None:
    assert truncate_result("short") == "short"


def test_truncate_result_long_capped() -> None:
    out = truncate_result("x" * 100, cap=10)
    assert out.startswith("x" * 10)
    assert "truncated" in out


def test_parse_arguments_empty_to_dict() -> None:
    assert parse_arguments("") == {}
    assert parse_arguments("{}") == {}


def test_parse_arguments_invalid_raises() -> None:
    with pytest.raises(ProviderProtocolError):
        parse_arguments("not json")


async def test_single_answer_no_tools() -> None:
    provider = ScriptedProvider([_single_turn("Hello there")])
    calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    result = await loop.run_turn("hi")

    assert isinstance(result, TurnResult)
    assert result.text == "Hello there"
    assert result.stopped == "end_turn"
    assert result.iterations == 1
    assert result.tool_calls == 0
    assert calls == []
    assert [(m.role, m.content) for m in loop.history] == [
        ("user", "hi"),
        ("assistant", "Hello there"),
    ]


async def test_multiple_text_deltas_join() -> None:
    provider = ScriptedProvider(
        [[TextDelta("Hello "), TextDelta("world"), Done("end_turn")]]
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    result = await loop.run_turn("go")
    assert result.text == "Hello world"


async def test_tool_call_then_final_answer() -> None:
    provider = ScriptedProvider(
        [
            [
                TextDelta("Let me check. "),
                ToolCallEvent(id="c1", name="eval_ruby", arguments='{"code":"1"}'),
                Done("tool_use"),
            ],
            _single_turn("The answer is 1."),
        ]
    )
    calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    result = await loop.run_turn("compute")

    assert result.text == "Let me check. \n\nThe answer is 1."
    assert result.stopped == "end_turn"
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert calls == [("eval_ruby", {"code": "1"})]
    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert loop.history[2].tool_call_id == "c1"
    assert loop.history[2].content == '{"ok": true}'


async def test_parallel_tool_calls_one_turn() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallEvent(id="c1", name="eval_ruby", arguments="{}"),
                ToolCallEvent(id="c2", name="eval_ruby", arguments="{}"),
                Done("tool_use"),
            ],
            _single_turn("done"),
        ]
    )
    calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    result = await loop.run_turn("x")

    assert result.tool_calls == 2
    assert len(calls) == 2
    assistant = [m for m in loop.history if m.role == "assistant"][0]
    assert [tc.id for tc in assistant.tool_calls] == ["c1", "c2"]


async def test_max_iterations_guard_stops_loop() -> None:
    tool_turn = [
        ToolCallEvent(id="c", name="eval_ruby", arguments="{}"),
        Done("tool_use"),
    ]
    provider = ScriptedProvider([list(tool_turn) for _ in range(5)])
    calls, execute = await _recorder()
    loop = _make_loop(provider, execute, max_iterations=2)

    result = await loop.run_turn("keep going")

    assert result.stopped == "tool_limit"
    assert result.iterations == 2
    assert result.tool_calls == 2
    assert len(calls) == 2


async def test_usage_reported_when_present() -> None:
    usage = Usage(input_tokens=5, output_tokens=7, total_tokens=12)
    provider = ScriptedProvider([_single_turn("hi", usage)])
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    result = await loop.run_turn("hi")
    assert result.usage == usage


async def test_images_attached_to_user_message() -> None:
    provider = ScriptedProvider([_single_turn("ok")])
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)
    image = ImagePart(media_type="image/png", data="aGk=")

    await loop.run_turn("look", images=[image])

    assert loop.history[0].role == "user"
    assert loop.history[0].images == [image]


async def test_tool_result_is_truncated() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallEvent(id="c1", name="eval_ruby", arguments="{}"),
                Done("tool_use"),
            ],
            _single_turn("ok"),
        ]
    )
    calls = []

    async def execute(name: str, arguments: dict) -> str:
        calls.append(name)
        return "x" * 100

    loop = _make_loop(provider, execute, max_result_chars=20)

    await loop.run_turn("go")

    tool_msg = next(m for m in loop.history if m.role == "tool")
    assert len(tool_msg.content) < 100
    assert "truncated" in tool_msg.content


def test_drop_dangling_tool_calls_removes_unanswered() -> None:
    history = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="eval_ruby", arguments="{}")],
        ),
    ]
    AgentLoop._drop_dangling_tool_calls(history)
    assert [m.role for m in history] == ["user"]


async def test_drop_dangling_tool_calls_noop_on_tool_result() -> None:
    history = [
        Message(role="user", content="hi"),
        Message(
            role="assistant", content="", tool_calls=[ToolCall("c1", "eval_ruby", "{}")]
        ),
        Message(role="tool", content="ok", tool_call_id="c1"),
    ]
    AgentLoop._drop_dangling_tool_calls(history)
    assert len(history) == 3


async def test_openai_encode_of_loop_history() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallEvent(id="c1", name="eval_ruby", arguments="{}"),
                Done("tool_use"),
            ],
            _single_turn("final"),
        ]
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)
    await loop.run_turn("first user turn")

    encoded = _openai_encode(loop.history, None)
    roles = [msg["role"] for msg in encoded]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assistant = next(m for m in encoded if m["role"] == "assistant")
    tool_calls = assistant["tool_calls"]
    assert tool_calls[0]["function"]["name"] == "eval_ruby"
    assert tool_calls[0]["function"]["arguments"] == "{}"
    tool_msg = next(m for m in encoded if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == '{"ok": true}'


def _tool_turn(*events: object) -> list[object]:
    return [*events, Done("tool_use")]


def _visual_extractor(images: list[ImagePart]):
    def extract(name: str, arguments: dict, result: str) -> list[ImagePart]:
        return images

    return extract


async def test_anthropic_collect_visual_rides_tool_message() -> None:
    image = ImagePart(media_type="image/png", data="aW1n")
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
            _single_turn("looks good"),
        ],
        dialect="anthropic",
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute, collect_visual=_visual_extractor([image]))

    await loop.run_turn("go")

    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    tool_msg = loop.history[2]
    assert tool_msg.images == [image]
    assert tool_msg.content == '{"ok": true}'
    assert _VISUAL_NOTE not in " ".join(m.content for m in loop.history)


async def test_openai_collect_visual_uses_synthetic_user_message() -> None:
    image = ImagePart(media_type="image/png", data="aW1n")
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
            _single_turn("looks good"),
        ],
        dialect="openai",
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute, collect_visual=_visual_extractor([image]))

    await loop.run_turn("go")

    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "tool", "user", "assistant"]
    tool_msg = loop.history[2]
    assert tool_msg.images == []
    visual_msg = loop.history[3]
    assert visual_msg.role == "user"
    assert visual_msg.images == [image]
    assert visual_msg.content == _VISUAL_NOTE


async def test_no_collect_visual_leaves_history_unchanged() -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
            _single_turn("done"),
        ],
        dialect="openai",
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)

    await loop.run_turn("go")

    roles = [m.role for m in loop.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert loop.history[2].images == []


async def test_empty_collect_visual_returns_are_noops() -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
            _single_turn("done"),
        ],
        dialect="openai",
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute, collect_visual=_visual_extractor([]))

    await loop.run_turn("go")

    assert [m.role for m in loop.history] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


async def test_anthropic_encode_loop_history_with_tool_images() -> None:
    image = ImagePart(media_type="image/png", data="aW1n")
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
            _single_turn("ok"),
        ],
        dialect="anthropic",
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute, collect_visual=_visual_extractor([image]))
    await loop.run_turn("go")

    _system, encoded = _anthropic_encode(loop.history, None)
    roles = [msg["role"] for msg in encoded]
    assert roles == ["user", "assistant", "user", "assistant"]
    tool_user = encoded[2]
    assert tool_user["role"] == "user"
    block = tool_user["content"][0]
    assert block["type"] == "tool_result"
    content = block["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": '{"ok": true}'}
    assert content[1]["type"] == "image"
    assert content[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aW1n",
    }


async def test_anthropic_encode_of_loop_history_alternates() -> None:
    provider = ScriptedProvider(
        [
            [
                ToolCallEvent(id="c1", name="eval_ruby", arguments='{"code":"1"}'),
                Done("tool_use"),
            ],
            _single_turn("final words"),
        ]
    )
    _calls, execute = await _recorder()
    loop = _make_loop(provider, execute)
    await loop.run_turn("hello")

    system, encoded = _anthropic_encode(loop.history, "sys")
    assert system == "sys"
    roles = [msg["role"] for msg in encoded]
    assert roles == ["user", "assistant", "user", "assistant"]
    assistant = encoded[1]
    assert assistant["content"][0]["type"] == "tool_use"
    assert assistant["content"][0]["id"] == "c1"
    assert assistant["content"][0]["name"] == "eval_ruby"
    assert assistant["content"][0]["input"] == {"code": "1"}
    tool_user = encoded[2]
    assert tool_user["content"][0]["type"] == "tool_result"
    assert tool_user["content"][0]["tool_use_id"] == "c1"
    assert tool_user["content"][0]["content"] == '{"ok": true}'


async def test_on_tool_result_reports_each_execution() -> None:
    image = ImagePart(media_type="image/png", data="aW1n")
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCallEvent(id="c1", name="eval_ruby", arguments='{"code":"1"}'),
                ToolCallEvent(id="c2", name="read_file", arguments='{"path":"a.rb"}'),
            ),
            _single_turn("done"),
        ],
        dialect="anthropic",
    )
    _calls, execute = await _recorder()
    observed = []
    loop = _make_loop(
        provider,
        execute,
        on_tool_result=lambda tool_id, name, arguments, result, images: observed.append(
            (tool_id, name, arguments, result, list(images))
        ),
        collect_visual=_visual_extractor([image]),
    )

    await loop.run_turn("go")

    assert len(observed) == 2
    first = observed[0]
    assert first[0] == "c1"
    assert first[1] == "eval_ruby"
    assert first[2] == {"code": "1"}
    assert first[3] == '{"ok": true}'
    assert first[4] == [image]
    second = observed[1]
    assert second[0] == "c2"
    assert second[1] == "read_file"
    assert second[2] == {"path": "a.rb"}
    assert second[3] == '{"ok": true}'
    assert second[4] == [image]


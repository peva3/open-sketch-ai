"""Tests for the public Agent facade wiring providers/backends/loop."""

from __future__ import annotations

import json

import pytest

import supex_driver.agent.agent as agent_mod
from supex_driver.agent.agent import Agent
from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.providers.base import Done, TextDelta, ToolCallEvent, ToolSchema
from tests.agent.fakes import FakeBackend, ScriptedProvider


def _config(**overrides) -> ProviderConfig:
    kwargs = {
        "base_url": "http://localhost:1",
        "api_key": "test-key",
        "model": "test-model",
        "dialect": "openai",
        "max_iterations": 8,
    }
    kwargs.update(overrides)
    return ProviderConfig(**kwargs)


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: fake)
    return fake


def _final(text: str) -> list[object]:
    return [TextDelta(text), Done("end_turn")]


def _tool_turn(*calls: ToolCallEvent) -> list[object]:
    return [*calls, Done("tool_use")]


async def test_tools_merge_file_and_sketchup(backend, tmp_path) -> None:
    backend.add_tool(ToolSchema(name="eval_ruby", description="run ruby"))
    backend.add_tool(ToolSchema(name="get_model_info", description="model info"))
    agent = Agent(config=_config(), provider=ScriptedProvider(), workspace=tmp_path)

    tools = await agent.tools()

    names = {t.name for t in tools}
    assert {
        "eval_ruby",
        "get_model_info",
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "delete_file",
    } <= names
    await agent.aclose()


async def test_run_turn_routes_sketchup_tool(backend, tmp_path) -> None:
    backend.add_tool(ToolSchema(name="eval_ruby", description="run ruby"))
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCallEvent(id="c1", name="eval_ruby", arguments='{"code":"1"}')
            ),
            _final("computed"),
        ]
    )
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)

    result = await agent.run_turn("please compute")

    assert result.text == "computed"
    assert result.stopped == "end_turn"
    assert backend.calls == [("eval_ruby", {"code": "1"})]


async def test_run_turn_reads_workspace_file(backend, tmp_path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello from file")
    backend.add_tool(ToolSchema(name="eval_ruby", description="run ruby"))
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCallEvent(
                    id="c1", name="read_file", arguments='{"path":"notes.txt"}'
                )
            ),
            _final("read it"),
        ]
    )
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)

    await agent.run_turn("read notes")

    tool_msg = next(m for m in agent.history if m.role == "tool")
    assert tool_msg.tool_call_id == "c1"
    assert tool_msg.content == "hello from file"
    assert backend.calls == []


async def test_run_turn_blocks_write_outside_workspace(backend, tmp_path) -> None:
    outside = tmp_path.parent / "secret.txt"
    provider = ScriptedProvider(
        [
            _tool_turn(
                ToolCallEvent(
                    id="c1",
                    name="write_file",
                    arguments=json.dumps({"path": str(outside), "content": "nope"}),
                )
            ),
            _final("attempted"),
        ]
    )
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)

    await agent.run_turn("write outside")

    tool_msg = next(m for m in agent.history if m.role == "tool")
    payload = json.loads(tool_msg.content)
    assert payload["ok"] is False
    assert not outside.exists()


async def test_run_turn_unknown_tool_reports_error(backend, tmp_path) -> None:
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="bogus_tool", arguments="{}")),
            _final("whatever"),
        ]
    )
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)

    await agent.run_turn("do the thing")

    tool_msg = next(m for m in agent.history if m.role == "tool")
    payload = json.loads(tool_msg.content)
    assert payload["ok"] is False
    assert "unknown tool" in payload["error"]


async def test_run_turn_backend_error_is_caught(backend, tmp_path) -> None:
    backend.add_tool(ToolSchema(name="explode", description="explodes"))
    provider = ScriptedProvider(
        [
            _tool_turn(ToolCallEvent(id="c1", name="explode", arguments="{}")),
            _final("recovered"),
        ]
    )
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)

    result = await agent.run_turn("make it explode")

    assert result.text == "recovered"
    tool_msg = next(m for m in agent.history if m.role == "tool")
    assert "failed remotely" in tool_msg.content


async def test_history_and_reset(backend, tmp_path) -> None:
    backend.add_tool(ToolSchema(name="eval_ruby", description="run ruby"))
    agent = Agent(
        config=_config(),
        provider=ScriptedProvider(
            [
                _tool_turn(ToolCallEvent(id="c1", name="eval_ruby", arguments="{}")),
                _final("done"),
            ]
        ),
        workspace=tmp_path,
    )
    assert agent.history == []
    await agent.run_turn("first")
    assert [m.role for m in agent.history] == ["user", "assistant", "tool", "assistant"]

    agent.reset_conversation()
    assert agent.history == []


async def test_aclose_closes_backend_and_provider(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    agent = Agent(config=_config(), provider=provider, workspace=tmp_path)
    await agent.aclose()
    assert backend.closed is True
    assert provider.closed is True

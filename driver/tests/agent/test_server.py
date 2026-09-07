"""Tests for the local HTTP AgentServer (windowed-chat backend).

These start a real ``AgentServer`` bound to an ephemeral loopback port and
drive it over HTTP with httpx2, asserting on the NDJSON wire protocol. The
SketchUp backend is a scripted fake (no subprocess) and the provider is the
shared ``ScriptedProvider``.
"""

from __future__ import annotations

import base64
import json
import socket

import httpx2
import pytest

import supex_driver.agent.agent as agent_mod
from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.providers.base import (
    Done,
    TextDelta,
    ToolCallEvent,
    ToolSchema,
)
from supex_driver.agent.server import AgentServer
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


class _ModelsProvider(ScriptedProvider):
    """Scripted provider that also reports a static model list."""

    async def list_models(self) -> list[str]:
        return ["local-model-a", "local-model-b"]


class _ShotBackend(FakeBackend):
    """Backend whose screenshot tool reports a real file under the workspace."""

    def __init__(self, shot_path) -> None:
        super().__init__([ToolSchema(name="take_screenshot", description="shot")])
        self._shot_path = str(shot_path)

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "explode":
            from supex_driver.agent.errors import BackendToolError

            raise BackendToolError(name, "remote boom")
        return json.dumps({"ok": True, "path": self._shot_path})


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: fake)
    return fake


def _start_server(
    config: ProviderConfig,
    *,
    provider,
    workspace,
    backend=None,
) -> AgentServer:
    if backend is not None:
        backend.add_tool(ToolSchema(name="eval_ruby", description="run ruby"))
    server = AgentServer(
        config=config,
        provider=provider,
        workspace=workspace,
        host="127.0.0.1",
        port=_free_port(),
    )
    server.start()
    return server


def _stop_server(server: AgentServer) -> None:
    server.stop()


async def _chat_lines(
    client: httpx2.AsyncClient, url: str, payload: dict
) -> list[dict]:
    """POST a chat turn and parse the NDJSON response into dicts."""
    lines: list[dict] = []
    async with client.stream("POST", f"{url}/api/chat", json=payload) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        async for line in resp.aiter_lines():
            if line.strip():
                lines.append(json.loads(line))
    return lines


def _final(text: str) -> list[object]:
    return [TextDelta(text), Done("end_turn")]


def _tool_turn(*calls: ToolCallEvent) -> list[object]:
    return [*calls, Done("tool_use")]


async def test_health_reports_config(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.get(f"{server.url}/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["model"] == "test-model"
        assert body["dialect"] == "openai"
        assert body["vision"] is False
        assert body["workspace"] == str(tmp_path.resolve())
    finally:
        _stop_server(server)


async def test_index_serves_chat_ui(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.get(f"{server.url}/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "supex-chat" in resp.text
    finally:
        _stop_server(server)


async def test_models_endpoint_lists_provider_ids(backend, tmp_path) -> None:
    provider = _ModelsProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.get(f"{server.url}/api/models")
        assert resp.status_code == 200
        assert resp.json() == {"models": ["local-model-a", "local-model-b"]}
    finally:
        _stop_server(server)


async def test_unknown_path_returns_404(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.get(f"{server.url}/api/nope")
        assert resp.status_code == 404
        assert resp.json()["ok"] is False
    finally:
        _stop_server(server)


async def test_chat_streams_delta_then_done(backend, tmp_path) -> None:
    provider = ScriptedProvider(turns=[_final("hi there")])
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            lines = await _chat_lines(client, server.url, {"text": "hello"})
    finally:
        _stop_server(server)

    assert lines[0]["type"] == "delta"
    assert lines[0]["text"] == "hi there"
    assert any(line["type"] == "model_end" for line in lines)
    done = lines[-1]
    assert done["type"] == "done"
    assert done["text"] == "hi there"
    assert done["stopped"] == "end_turn"
    assert done["tool_calls"] == 0


async def test_chat_tool_turn_emits_tool_lines(backend, tmp_path) -> None:
    provider = ScriptedProvider(
        turns=[
            _tool_turn(
                ToolCallEvent(id="c1", name="eval_ruby", arguments='{"code":"1"}')
            ),
            _final("computed"),
        ]
    )
    server = _start_server(
        _config(), provider=provider, workspace=tmp_path, backend=backend
    )
    try:
        async with httpx2.AsyncClient() as client:
            lines = await _chat_lines(client, server.url, {"text": "please compute"})
    finally:
        _stop_server(server)

    call = next(line for line in lines if line["type"] == "tool_call")
    assert call["id"] == "c1"
    assert call["name"] == "eval_ruby"
    assert call["arguments"] == '{"code":"1"}'

    result = next(line for line in lines if line["type"] == "tool_result")
    assert result["id"] == "c1"
    assert result["name"] == "eval_ruby"
    assert result["result"] == '{"ok": true}'
    assert result["images"] == []

    done = lines[-1]
    assert done["type"] == "done"
    assert done["text"] == "computed"
    assert done["stopped"] == "end_turn"
    assert done["tool_calls"] == 1


@pytest.mark.parametrize("dialect", ["openai", "anthropic"])
async def test_chat_surfaces_screenshot_image(monkeypatch, tmp_path, dialect) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png_bytes())
    backend = _ShotBackend(shot)
    monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: backend)
    provider = ScriptedProvider(
        turns=[
            _tool_turn(
                ToolCallEvent(
                    id="c1", name="take_screenshot", arguments='{"path": "shot.png"}'
                )
            ),
            _final("looks good"),
        ],
        dialect=dialect,
    )
    config = _config(dialect=dialect, vision=True)
    server = _start_server(config, provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            lines = await _chat_lines(client, server.url, {"text": "show me"})
    finally:
        _stop_server(server)

    result = next(line for line in lines if line["type"] == "tool_result")
    images = result["images"]
    assert len(images) == 1
    assert images[0]["media_type"] == "image/png"
    assert images[0]["data"] == base64.b64encode(_png_bytes()).decode("ascii")

    done = lines[-1]
    assert done["type"] == "done"
    assert done["text"] == "looks good"


async def test_chat_rejects_empty_text(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.post(f"{server.url}/api/chat", json={"text": "   "})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
    finally:
        _stop_server(server)


async def test_chat_rejects_malformed_json(backend, tmp_path) -> None:
    provider = ScriptedProvider()
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            resp = await client.post(
                f"{server.url}/api/chat",
                content=b"{not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False
    finally:
        _stop_server(server)


async def test_reset_clears_conversation(backend, tmp_path) -> None:
    provider = ScriptedProvider(turns=[_final("first reply")])
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            await _chat_lines(client, server.url, {"text": "first"})
            assert len(server.agent.history) > 0
            resp = await client.post(f"{server.url}/api/reset")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert server.agent.history == []
    finally:
        _stop_server(server)


async def test_sequential_chats_both_succeed(backend, tmp_path) -> None:
    provider = ScriptedProvider(turns=[_final("one"), _final("two")])
    server = _start_server(_config(), provider=provider, workspace=tmp_path)
    try:
        async with httpx2.AsyncClient() as client:
            first = await _chat_lines(client, server.url, {"text": "first"})
            second = await _chat_lines(client, server.url, {"text": "second"})
        assert first[-1]["type"] == "done"
        assert first[-1]["text"] == "one"
        assert second[-1]["type"] == "done"
        assert second[-1]["text"] == "two"
    finally:
        _stop_server(server)

"""Provider streaming tests against recorded SSE fixtures (no network).

Drives OpenAI/Anthropic providers through an httpx2 ``MockTransport``,
verifying delta assembly, tool-call buffering, stop-reason/usage mapping,
auth header selection, model listing, and error surfacing.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderRateLimitError,
)
from supex_driver.agent.providers import build_provider
from supex_driver.agent.providers.anthropic import AnthropicProvider
from supex_driver.agent.providers.base import (
    ChatProvider,
    Done,
    ImagePart,
    Message,
    TextDelta,
    ToolCallEvent,
    ToolSchema,
    Usage,
)
from supex_driver.agent.providers.openai import OpenAIProvider

EVENT_HEADERS = {"content-type": "text/event-stream"}


def _openai_config(**overrides) -> ProviderConfig:
    base = {
        "base_url": "http://localhost:8000/v1",
        "api_key": "sk-test",
        "model": "test-model",
        "dialect": "openai",
        "timeout": 5.0,
    }
    base.update(overrides)
    return ProviderConfig(**base)


def _anthropic_config(**overrides) -> ProviderConfig:
    base = {
        "base_url": "http://localhost:8000",
        "api_key": "sk-ant-test",
        "model": "claude-test",
        "dialect": "anthropic",
        "timeout": 5.0,
    }
    base.update(overrides)
    return ProviderConfig(**base)


def _json_response(body: dict | list | str, status: int = 200) -> httpx2.Response:
    content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    headers = EVENT_HEADERS if status == 200 else {"content-type": "application/json"}
    return httpx2.Response(status_code=status, headers=headers, content=content)


def _sse(*events: str) -> str:
    """Join raw SSE lines/events into a single body."""
    return "\n".join(events) + "\n"


def _data(obj) -> str:
    return f"data: {json.dumps(obj)}"


def _install_transport(provider: ChatProvider, handler) -> httpx2.AsyncClient:
    transport = httpx2.MockTransport(handler)
    client = httpx2.AsyncClient(transport=transport, timeout=httpx2.Timeout(5.0))
    provider._client = client  # noqa: SLF001 - test seam
    return client


async def _disable_backoff(provider: ChatProvider) -> None:
    """Replace the provider's backoff sleep with a no-op for fast retry tests."""

    async def noop(attempt: int) -> None:  # noqa: ARG001
        return None

    provider._backoff = noop  # type: ignore[attr-defined] # noqa: SLF001 - test seam


async def _collect(provider: ChatProvider, messages, **kwargs) -> list:
    events: list = []
    async for event in provider.stream(messages, **kwargs):
        events.append(event)
    return events


class TestOpenAIStream:
    @pytest.mark.asyncio
    async def test_text_stream(self):
        body = _sse(
            _data(
                {"choices": [{"delta": {"content": "Hello "}, "finish_reason": None}]}
            ),
            _data(
                {"choices": [{"delta": {"content": "world"}, "finish_reason": None}]}
            ),
            _data({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        )
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(
                provider, [Message(role="user", content="hi")], tools=None
            )
        finally:
            await provider.aclose()

        assert [e.text for e in events if isinstance(e, TextDelta)] == [
            "Hello ",
            "world",
        ]
        done = events[-1]
        assert isinstance(done, Done)
        assert done.stop_reason == "end_turn"
        assert captured["url"].endswith("/chat/completions")
        body = captured["body"]
        assert body["model"] == "test-model"
        assert body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_fragmented_tool_call(self):
        body = _sse(
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "eval_ruby",
                                            "arguments": "",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '{"code": "Sketchup'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '.version"}'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(
                provider, [Message(role="user", content="check version")]
            )
        finally:
            await provider.aclose()

        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].id == "call_1"
        assert calls[0].name == "eval_ruby"
        assert calls[0].arguments == '{"code": "Sketchup.version"}'
        done = events[-1]
        assert isinstance(done, Done)
        assert done.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_cumulative_argument_resend(self):
        # A server that resends the accumulated JSON must not double-append.
        body = _sse(
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {
                                            "name": "f",
                                            "arguments": '{"a": ',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '{"a": 1}'}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(provider, [Message(role="user", content="go")])
        finally:
            await provider.aclose()

        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].arguments == '{"a": 1}'

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_sorted(self):
        body = _sse(
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "c2",
                                        "function": {
                                            "name": "second",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "first",
                                            "arguments": "{}",
                                        },
                                    },
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _data({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]",
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(provider, [Message(role="user", content="go")])
        finally:
            await provider.aclose()

        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert [c.name for c in calls] == ["first", "second"]
        assert [c.id for c in calls] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_usage_and_max_tokens_stop(self):
        body = _sse(
            _data({"choices": [{"delta": {"content": "x"}, "finish_reason": None}]}),
            _data({"choices": [{"delta": {}, "finish_reason": "length"}]}),
            _data(
                {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                }
            ),
            "data: [DONE]",
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

        done = events[-1]
        assert isinstance(done, Done)
        assert done.stop_reason == "max_tokens"
        assert done.usage == Usage(input_tokens=10, output_tokens=5, total_tokens=15)

    @pytest.mark.asyncio
    async def test_error_chunk_raises(self):
        body = _sse(_data({"error": {"message": "model overloaded"}}))

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderError, match="model overloaded"):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

    @pytest.mark.asyncio
    async def test_malformed_json_raises_protocol_error(self):
        body = _sse("data: {not json")

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderProtocolError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()


class TestOpenAIAuthAndErrors:
    @pytest.mark.asyncio
    async def test_bearer_header_sent(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["auth"] = request.headers.get("authorization")
            return _json_response(_sse("data: [DONE]"))

        provider = build_provider(_openai_config(api_key="sk-secret"))
        _install_transport(provider, handler)
        try:
            await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

        assert captured["auth"] == "Bearer sk-secret"

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response({"error": "invalid key"}, status=401)

        provider = build_provider(_openai_config(retries=0))
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderAuthError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response({"error": "slow down"}, status=429)

        provider = build_provider(_openai_config(retries=0))
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderRateLimitError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

    @pytest.mark.asyncio
    async def test_500_raises_http_error_with_status(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response({"error": "boom"}, status=500)

        provider = build_provider(_openai_config(retries=0))
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderHTTPError) as exc:
                await _collect(provider, [Message(role="user", content="hi")])
            assert exc.value.status_code == 500
        finally:
            await provider.aclose()


class TestModelListing:
    @pytest.mark.asyncio
    async def test_openai_list_models(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["url"] = str(request.url)
            return _json_response({"data": [{"id": "a"}, {"id": "b"}]})

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            models = await provider.list_models()
        finally:
            await provider.aclose()

        assert models == ["a", "b"]
        assert captured["url"].endswith("/models")

    @pytest.mark.asyncio
    async def test_openai_list_models_missing_data(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response({"foo": 1})

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            models = await provider.list_models()
        finally:
            await provider.aclose()

        assert models == []

    @pytest.mark.asyncio
    async def test_openai_list_models_404_empty(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(status_code=404)

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        try:
            models = await provider.list_models()
        finally:
            await provider.aclose()

        assert models == []


class TestAnthropicStream:
    @pytest.mark.asyncio
    async def test_text_stream_with_usage(self):
        body = _sse(
            _data({"type": "message_start", "message": {"usage": {"input_tokens": 7}}}),
            _data(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
            _data(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hi"},
                }
            ),
            _data(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": " there"},
                }
            ),
            _data({"type": "content_block_stop", "index": 0}),
            _data(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 4},
                }
            ),
            _data({"type": "message_stop"}),
        )
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return _json_response(body)

        provider = build_provider(_anthropic_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(
                provider, [Message(role="user", content="hi")], system="be brief"
            )
        finally:
            await provider.aclose()

        assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hi", " there"]
        done = events[-1]
        assert isinstance(done, Done)
        assert done.stop_reason == "end_turn"
        assert done.usage == Usage(input_tokens=7, output_tokens=4, total_tokens=11)
        assert captured["url"].endswith("/v1/messages")
        body = captured["body"]
        assert body["system"] == "be brief"
        assert body["max_tokens"] == 4096
        assert body["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]}
        ]

    @pytest.mark.asyncio
    async def test_tool_call_with_input_json_delta(self):
        body = _sse(
            _data({"type": "message_start", "message": {"usage": {"input_tokens": 9}}}),
            _data(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "eval_ruby",
                        "input": {},
                    },
                }
            ),
            _data(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"code":'},
                }
            ),
            _data(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": ' "1 + 1"}'},
                }
            ),
            _data({"type": "content_block_stop", "index": 0}),
            _data(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 3},
                }
            ),
            _data({"type": "message_stop"}),
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_anthropic_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(provider, [Message(role="user", content="compute")])
        finally:
            await provider.aclose()

        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].id == "toolu_1"
        assert calls[0].name == "eval_ruby"
        assert calls[0].arguments == '{"code": "1 + 1"}'
        done = events[-1]
        assert isinstance(done, Done)
        assert done.stop_reason == "tool_use"
        assert done.usage == Usage(input_tokens=9, output_tokens=3, total_tokens=12)

    @pytest.mark.asyncio
    async def test_prefilled_tool_input_no_delta(self):
        body = _sse(
            _data({"type": "message_start", "message": {"usage": {"input_tokens": 9}}}),
            _data(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "get_entity",
                        "input": {"entity_id": 3},
                    },
                }
            ),
            _data({"type": "content_block_stop", "index": 0}),
            _data({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
            _data({"type": "message_stop"}),
        )

        async def handler(request: httpx2.Request) -> httpx2.Response:
            return _json_response(body)

        provider = build_provider(_anthropic_config())
        _install_transport(provider, handler)
        try:
            events = await _collect(provider, [Message(role="user", content="inspect")])
        finally:
            await provider.aclose()

        calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(calls) == 1
        assert calls[0].arguments == '{"entity_id": 3}'


class TestAnthropicAuth:
    @pytest.mark.asyncio
    async def test_x_api_key_for_anthropic_host(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["headers"] = dict(request.headers)
            return _json_response("")

        provider = build_provider(
            _anthropic_config(base_url="https://api.anthropic.com")
        )
        _install_transport(provider, handler)
        try:
            await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

        headers = captured["headers"]
        assert headers.get("x-api-key") == "sk-ant-test"
        assert headers.get("anthropic-version") == "2023-06-01"
        assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_bearer_for_local_host(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["headers"] = dict(request.headers)
            return _json_response("")

        provider = build_provider(_anthropic_config())  # localhost:8000
        _install_transport(provider, handler)
        try:
            await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

        headers = captured["headers"]
        assert headers.get("authorization") == "Bearer sk-ant-test"
        assert "x-api-key" not in headers

    @pytest.mark.asyncio
    async def test_forced_x_api_key_on_local(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["headers"] = dict(request.headers)
            return _json_response("")

        provider = build_provider(_anthropic_config(auth_header="x-api-key"))
        _install_transport(provider, handler)
        try:
            await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()

        assert captured["headers"].get("x-api-key") == "sk-ant-test"
        assert "authorization" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_anthropic_list_models(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["url"] = str(request.url)
            return _json_response({"data": [{"id": "claude-x"}, {"id": "claude-y"}]})

        provider = build_provider(_anthropic_config())
        _install_transport(provider, handler)
        try:
            models = await provider.list_models()
        finally:
            await provider.aclose()

        assert models == ["claude-x", "claude-y"]
        assert captured["url"].endswith("/v1/models")


class TestToolSchema:
    def test_to_openai(self):
        schema = ToolSchema(
            name="eval_ruby",
            description="run ruby",
            input_schema={"type": "object", "properties": {"code": {"type": "string"}}},
        )
        assert schema.to_openai() == {
            "type": "function",
            "function": {
                "name": "eval_ruby",
                "description": "run ruby",
                "parameters": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                },
            },
        }

    def test_to_anthropic(self):
        schema = ToolSchema(name="eval_ruby", description="run ruby", input_schema={})
        assert schema.to_anthropic() == {
            "name": "eval_ruby",
            "description": "run ruby",
            "input_schema": {},
        }


class TestProviderSelection:
    def test_build_provider_openai(self):
        provider = build_provider(_openai_config())
        assert isinstance(provider, OpenAIProvider)
        assert isinstance(provider, ChatProvider)

    def test_build_provider_anthropic(self):
        provider = build_provider(_anthropic_config())
        assert isinstance(provider, AnthropicProvider)

    def test_provider_dialect_property(self):
        assert build_provider(_openai_config()).dialect == "openai"
        assert build_provider(_anthropic_config()).dialect == "anthropic"


class TestImageEncodingOpenAI:
    @pytest.mark.asyncio
    async def test_image_content_block(self):
        captured: dict = {}

        async def handler(request: httpx2.Request) -> httpx2.Response:
            captured["body"] = json.loads(request.content)
            return _json_response(_sse("data: [DONE]"))

        provider = build_provider(_openai_config())
        _install_transport(provider, handler)
        msg = Message(
            role="user",
            content="what is in this?",
            images=[ImagePart(media_type="image/png", data="aGVsbG8=")],
        )
        try:
            await _collect(provider, [msg])
        finally:
            await provider.aclose()

        content = captured["body"]["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "what is in this?"}
        assert content[1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
        }


class TestConnectionFailures:
    @pytest.mark.asyncio
    async def test_openai_connect_error(self):
        async def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("connection refused")

        provider = build_provider(_openai_config(retries=0))
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderConnectionError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()


class TestRetries:
    @pytest.mark.asyncio
    async def test_connect_error_retries_then_raises(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            raise httpx2.ConnectError("still down")

        provider = build_provider(_openai_config(retries=2))
        _install_transport(provider, handler)
        await _disable_backoff(provider)
        try:
            with pytest.raises(ProviderConnectionError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert calls == 3

    @pytest.mark.asyncio
    async def test_transient_429_succeeds_after_retry(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return _json_response({"error": "slow down"}, status=429)
            return _json_response(_sse("data: [DONE]"))

        provider = build_provider(_openai_config(retries=2))
        _install_transport(provider, handler)
        await _disable_backoff(provider)
        try:
            events = await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert calls == 3
        assert isinstance(events[-1], Done)

    @pytest.mark.asyncio
    async def test_transient_500_succeeds_on_list_models(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _json_response({"error": "boom"}, status=500)
            return _json_response({"data": [{"id": "a"}]})

        provider = build_provider(_openai_config(retries=1))
        _install_transport(provider, handler)
        await _disable_backoff(provider)
        try:
            models = await provider.list_models()
        finally:
            await provider.aclose()
        assert calls == 2
        assert models == ["a"]

    @pytest.mark.asyncio
    async def test_auth_error_is_not_retried(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            return _json_response({"error": "bad key"}, status=401)

        provider = build_provider(_openai_config(retries=2))
        _install_transport(provider, handler)
        await _disable_backoff(provider)
        try:
            with pytest.raises(ProviderAuthError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_4xx_is_not_retried(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            return _json_response({"error": "bad request"}, status=400)

        provider = build_provider(_openai_config(retries=2))
        _install_transport(provider, handler)
        await _disable_backoff(provider)
        try:
            with pytest.raises(ProviderHTTPError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_zero_retries_raises_immediately(self):
        calls = 0

        async def handler(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            raise httpx2.ConnectError("refused")

        provider = build_provider(_openai_config(retries=0))
        _install_transport(provider, handler)
        try:
            with pytest.raises(ProviderConnectionError):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_headers_then_close_before_content_is_retried(self):
        """DeepSeek-style: 200 + SSE headers, then the socket closes before
        any body bytes. The request must be retried transparently."""
        from contextlib import asynccontextmanager

        attempts = 0

        async def aiter_lines_once() -> None:
            return None

        class _Stream:
            def __init__(self, *, fail_first: bool) -> None:
                self.fail_first = fail_first

            async def aiter_lines(self):
                if self.fail_first:
                    self.fail_first = False
                    raise httpx2.ReadError("closed before body")
                yield _data({"choices": [{"delta": {"content": "hello"}}]})
                yield _data({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                yield "data: [DONE]"

        @asynccontextmanager
        async def fake_stream_text(url, headers, payload):
            nonlocal attempts
            attempts += 1
            yield _Stream(fail_first=attempts == 1)

        provider = build_provider(_openai_config(retries=2))
        _install_transport(
            provider, lambda request: _json_response(_sse("data: [DONE]"))
        )
        await _disable_backoff(provider)
        provider._stream_text = fake_stream_text  # type: ignore[method-assign] # noqa: SLF001
        try:
            events = await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert attempts == 2
        assert isinstance(events[0], TextDelta)
        assert events[0].text == "hello"
        assert isinstance(events[-1], Done)

    @pytest.mark.asyncio
    async def test_mid_stream_drop_after_content_raises_typed_error(self):
        """A drop after content has started must NOT be retried (replay would
        duplicate output); it surfaces as a typed ProviderConnectionError."""
        from contextlib import asynccontextmanager

        attempts = 0

        class _DropStream:
            async def aiter_lines(self):
                yield _data({"choices": [{"delta": {"content": "partial"}}]})
                raise httpx2.ReadError("socket closed mid-stream")

        @asynccontextmanager
        async def fake_stream_text(url, headers, payload):
            nonlocal attempts
            attempts += 1
            yield _DropStream()

        provider = build_provider(_openai_config(retries=2))
        _install_transport(
            provider, lambda request: _json_response(_sse("data: [DONE]"))
        )
        await _disable_backoff(provider)
        provider._stream_text = fake_stream_text  # type: ignore[method-assign] # noqa: SLF001
        try:
            with pytest.raises(ProviderConnectionError, match="mid-stream"):
                await _collect(provider, [Message(role="user", content="hi")])
        finally:
            await provider.aclose()
        assert attempts == 1

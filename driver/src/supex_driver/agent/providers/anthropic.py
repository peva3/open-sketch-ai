"""Anthropic-compatible chat provider (``/v1/messages`` streaming)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from supex_driver.agent.errors import ProviderProtocolError
from supex_driver.agent.providers.base import (
    ChatProvider,
    Done,
    Message,
    TextDelta,
    ToolCallEvent,
    ToolSchema,
    extract_model_ids,
    normalize_stop_reason,
    parse_tool_arguments,
    url_join,
)

_DEFAULT_MAX_TOKENS = 4096
_VERSION_HEADER = "anthropic-version"
_VERSION = "2023-06-01"


def _auth_headers_anthropic(config: Any) -> dict[str, str]:
    """Return Anthropic auth headers honoring the configured auth style.

    Real Anthropic endpoints want ``x-api-key``; most local compatible
    servers (Unsloth, llama.cpp) accept ``Authorization: Bearer``. The
    ``auth_header`` config knob forces one; ``auto`` picks ``x-api-key``
    only for anthropic.com hosts, otherwise ``Bearer``.
    """
    headers: dict[str, str] = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        _VERSION_HEADER: _VERSION,
    }
    api_key = config.api_key
    if not api_key:
        return headers
    style = config.auth_header
    host = (config.base_url or "").lower()
    if style == "x-api-key" or (style == "auto" and "anthropic.com" in host):
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {api_key}"
    headers.update(config.extra_headers)
    return headers


def _content_block_text(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def _content_block_image(image: Any) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.media_type,
            "data": image.data,
        },
    }


def _encode_user_content(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append(_content_block_text(message.content))
    blocks.extend(_content_block_image(image) for image in message.images)
    return blocks


def _encode_messages(
    messages: Sequence[Message], system: str | None
) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate neutral messages to the Anthropic wire format.

    Returns ``(system, anthropic_messages)``. Consecutive ``tool``
    (result) messages are folded into a single user message carrying one
    ``tool_result`` block each, and adjacent same-role messages are
    merged to satisfy Anthropic's strict user/assistant alternation.
    """
    encoded: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        if not pending_results:
            return
        encoded.append(
            {
                "role": "user",
                "content": [{"type": "tool_result", **r} for r in pending_results],
            }
        )
        pending_results.clear()

    def append(msg: dict[str, Any]) -> None:
        if encoded and encoded[-1]["role"] == msg["role"]:
            if isinstance(encoded[-1]["content"], str) or isinstance(
                msg["content"], str
            ):
                text = f"{encoded[-1]['content']}\n\n{msg['content']}"
                encoded[-1] = {
                    "role": msg["role"],
                    "content": [{"type": "text", "text": text}],
                }
            else:
                encoded[-1]["content"].extend(msg["content"])
        else:
            encoded.append(msg)

    for message in messages:
        if message.role == "user":
            flush_results()
            content = _encode_user_content(message)
            if content:
                append({"role": "user", "content": content})
        elif message.role == "assistant":
            flush_results()
            if not message.content and not message.tool_calls:
                continue
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append(_content_block_text(message.content))
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": parse_tool_arguments(call.arguments),
                    }
                )
            append({"role": "assistant", "content": blocks})
        elif message.role == "tool":
            if not message.tool_call_id:
                raise ProviderProtocolError(
                    "tool result message is missing tool_call_id"
                )
            result_content: str | list[dict[str, Any]] = message.content
            if message.images:
                result_blocks: list[dict[str, Any]] = []
                if message.content:
                    result_blocks.append(_content_block_text(message.content))
                result_blocks.extend(
                    _content_block_image(image) for image in message.images
                )
                result_content = result_blocks
            pending_results.append(
                {
                    "tool_use_id": message.tool_call_id,
                    "content": result_content,
                }
            )
    flush_results()

    if encoded and encoded[0]["role"] != "user":
        raise ProviderProtocolError(
            "conversation must begin with a user message for the Anthropic API"
        )
    return system, encoded


class AnthropicProvider(ChatProvider):
    """Streaming client for Anthropic-compatible ``/v1/messages`` endpoints."""

    @property
    def _messages_url(self) -> str:
        base = url_join(self.config.base_url, "/v1/messages")
        return base

    @property
    def _models_url(self) -> str:
        return url_join(self.config.base_url, "/v1/models")

    def _payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None,
        *,
        model: str | None,
        system: str | None,
    ) -> dict[str, Any]:
        system_text, anthropic_messages = _encode_messages(messages, system)
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": anthropic_messages,
            "max_tokens": self.config.max_tokens or _DEFAULT_MAX_TOKENS,
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [tool.to_anthropic() for tool in tools]
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        return payload

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextDelta | ToolCallEvent | Done]:
        payload = self._payload(messages, tools, model=model, system=system)
        headers = _auth_headers_anthropic(self.config)

        tool_buffers: dict[int, dict[str, str]] = {}
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason: str | None = None

        async for line in self._stream_sse_lines(self._messages_url, headers, payload):
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if not data_text:
                continue
            try:
                data: dict[str, Any] = json.loads(data_text)
            except json.JSONDecodeError as exc:
                raise ProviderProtocolError(
                    f"non-JSON SSE data from provider: {data_text!r}"
                ) from exc
            event_type = data.get("type")
            if event_type == "message_start":
                usage = data.get("message", {}).get("usage") or {}
                input_tokens = usage.get("input_tokens")
            elif event_type == "content_block_start":
                block = data.get("content_block") or {}
                if block.get("type") == "tool_use":
                    index = int(data.get("index", 0))
                    # Seed only when arguments arrived whole; when the server
                    # streams input_json_delta it sends an empty `input`, and
                    # seeding "{}" would corrupt the accumulated fragment JSON.
                    raw_input = block.get("input")
                    seed = json.dumps(raw_input) if raw_input else ""
                    tool_buffers[index] = {
                        "id": block.get("id") or "",
                        "name": block.get("name") or "",
                        "input": seed,
                    }
            elif event_type == "content_block_delta":
                delta = data.get("delta") or {}
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text") or ""
                    if text:
                        yield TextDelta(text)
                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json") or ""
                    index = int(data.get("index", 0))
                    buffer = tool_buffers.setdefault(
                        index, {"id": "", "name": "", "input": ""}
                    )
                    current = buffer["input"]
                    if current and partial.startswith(current):
                        buffer["input"] = partial
                    else:
                        buffer["input"] = current + partial
            elif event_type == "message_delta":
                stop_reason = (data.get("delta") or {}).get("stop_reason")
                usage = data.get("usage") or {}
                output_tokens = usage.get("output_tokens")

        calls: list[ToolCallEvent] = []
        for index in sorted(tool_buffers):
            buffer = tool_buffers[index]
            if not buffer["name"]:
                continue
            parse_tool_arguments(buffer["input"])
            calls.append(
                ToolCallEvent(
                    id=buffer["id"] or f"call_{index}",
                    name=buffer["name"],
                    arguments=buffer["input"],
                )
            )
        for call in calls:
            yield call

        usage = None
        if input_tokens is not None or output_tokens is not None:
            from supex_driver.agent.providers.base import Usage

            usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(
                    None
                    if input_tokens is None or output_tokens is None
                    else input_tokens + output_tokens
                ),
            )
        yield Done(stop_reason=normalize_stop_reason(stop_reason), usage=usage)

    async def list_models(self) -> list[str]:
        headers = _auth_headers_anthropic(self.config)
        data = await self._get_json(self._models_url, headers)
        return extract_model_ids(data)

"""OpenAI-compatible chat provider (``/chat/completions`` streaming)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from supex_driver.agent.errors import ProviderError, ProviderProtocolError
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


def _encode_messages(
    messages: Sequence[Message], system: str | None
) -> list[dict[str, Any]]:
    encoded: list[dict[str, Any]] = []
    if system:
        encoded.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "user":
            if message.images:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for image in message.images:
                    blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image.media_type};base64,{image.data}"
                            },
                        }
                    )
                encoded.append({"role": "user", "content": blocks})
            else:
                encoded.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            if not message.content and not message.tool_calls:
                continue
            entry: dict[str, Any] = {"role": "assistant"}
            if message.tool_calls:
                entry["content"] = message.content or None
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in message.tool_calls
                ]
            else:
                entry["content"] = message.content
            encoded.append(entry)
        elif message.role == "tool":
            encoded.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
    return encoded


def _merge_argument_fragment(current: str, fragment: str) -> str:
    """Append an argument fragment, tolerating servers that resend cumulative JSON."""
    if not fragment:
        return current
    if current and fragment.startswith(current):
        return fragment
    return current + fragment


class OpenAIProvider(ChatProvider):
    """Streaming client for OpenAI-compatible chat-completions endpoints."""

    @property
    def _chat_url(self) -> str:
        return url_join(self.config.base_url, "/chat/completions")

    @property
    def _models_url(self) -> str:
        return url_join(self.config.base_url, "/models")

    def _payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None,
        *,
        model: str | None,
        system: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": _encode_messages(messages, system),
            "stream": True,
        }
        if tools:
            payload["tools"] = [tool.to_openai() for tool in tools]
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        return payload

    def _handle_error_chunk(self, data: dict[str, Any]) -> None:
        if isinstance(data.get("error"), dict):
            error = data["error"]
            message = error.get("message") or str(error)
            raise ProviderError(f"provider reported an error: {message}")

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[TextDelta | ToolCallEvent | Done]:
        payload = self._payload(messages, tools, model=model, system=system)
        headers = self._auth_headers()

        text_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}
        stop_reason: str | None = None
        usage: dict[str, Any] | None = None

        async with self._stream_text(self._chat_url, headers, payload) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    data: dict[str, Any] = json.loads(data_text)
                except json.JSONDecodeError as exc:
                    raise ProviderProtocolError(
                        f"non-JSON SSE data from provider: {data_text!r}"
                    ) from exc
                self._handle_error_chunk(data)
                if "usage" in data and isinstance(data["usage"], dict):
                    usage = data["usage"]
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                finish = choice.get("finish_reason")
                if finish:
                    stop_reason = finish
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield TextDelta(content)
                for tool_delta in delta.get("tool_calls") or []:
                    index = int(tool_delta.get("index", 0))
                    buffer = tool_buffers.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    fn = tool_delta.get("function") or {}
                    if tool_delta.get("id"):
                        buffer["id"] = tool_delta["id"]
                    if fn.get("name"):
                        buffer["name"] = fn["name"]
                    arguments = fn.get("arguments") or ""
                    buffer["arguments"] = _merge_argument_fragment(
                        buffer["arguments"], arguments
                    )

        for call in _finalize_tool_calls(tool_buffers):
            yield call
        yield Done(
            stop_reason=normalize_stop_reason(stop_reason),
            usage=_openai_usage(usage),
        )

    async def list_models(self) -> list[str]:
        headers = self._auth_headers()
        data = await self._get_json(self._models_url, headers)
        return extract_model_ids(data)


def _finalize_tool_calls(buffers: dict[int, dict[str, str]]) -> list[ToolCallEvent]:
    calls: list[ToolCallEvent] = []
    for index in sorted(buffers):
        buffer = buffers[index]
        if not buffer["name"]:
            continue
        arguments = buffer["arguments"]
        # Validate parseability eagerly so loop-level JSON bugs surface here.
        parse_tool_arguments(arguments)
        calls.append(
            ToolCallEvent(
                id=buffer["id"] or f"call_{index}",
                name=buffer["name"],
                arguments=arguments,
            )
        )
    return calls


def _openai_usage(raw: dict[str, Any] | None) -> Any:
    if not raw:
        return None
    from supex_driver.agent.providers.base import Usage

    return Usage(
        input_tokens=raw.get("prompt_tokens"),
        output_tokens=raw.get("completion_tokens"),
        total_tokens=raw.get("total_tokens"),
    )

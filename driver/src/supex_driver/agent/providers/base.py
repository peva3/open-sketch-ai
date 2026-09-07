"""Provider-agnostic chat abstractions for supex-chat.

Defines the neutral message/tool/event vocabulary shared by the OpenAI
and Anthropic providers, plus small HTTP helpers both dialects build on.
Everything here is transport-neutral; dialect-specific wire formats live
in :mod:`.openai` and :mod:`.anthropic`.
"""

from __future__ import annotations

import contextlib
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2

from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import (
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderTimeoutError,
)

Dialect = Literal["openai", "anthropic"]
Role = Literal["user", "assistant", "tool"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "other"]


@dataclass(frozen=True)
class ToolCall:
    """A single function-call the model requested."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolSchema:
    """Internal representation of one tool offered to the model."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        """Return the OpenAI ``tools[]`` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_anthropic(self) -> dict[str, Any]:
        """Return the Anthropic ``tools[]`` entry for this tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ImagePart:
    """Inline image content (base64, no ``data:`` prefix)."""

    media_type: str
    data: str


@dataclass
class Message:
    """A provider-neutral chat message.

    ``content`` holds text. ``tool_calls`` is only populated on assistant
    messages that requested tool use; ``tool_call_id`` only on ``tool``
    (result) messages. ``images`` optionally attaches inline vision input
    to a user message.
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    images: list[ImagePart] = field(default_factory=list)


@dataclass(frozen=True)
class TextDelta:
    """Incremental assistant text emitted during streaming."""

    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    """A completed tool-call the model made during this turn."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Usage:
    """Token accounting for a completed turn (may be partially None)."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class Done:
    """Marks the end of a model turn."""

    stop_reason: StopReason
    usage: Usage | None = None


ProviderEvent = TextDelta | ToolCallEvent | Done

_STOP_REASON_ALIASES: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "stop": "end_turn",
    "tool_use": "tool_use",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "max_tokens": "max_tokens",
    "length": "max_tokens",
    "pause_turn": "other",
    "content_filter": "other",
}


def normalize_stop_reason(raw: str | None) -> StopReason:
    """Map a provider's stop-reason string onto our small vocabulary."""
    if raw is None:
        return "other"
    return _STOP_REASON_ALIASES.get(raw, "other")


def url_join(base_url: str, path: str) -> str:
    """Append ``path`` to ``base_url`` unless it is already present."""
    base = base_url.rstrip("/")
    if base.endswith(path):
        return base
    return f"{base}{path}"


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse a raw tool-arguments JSON string, tolerating empty input."""
    text = raw.strip() or "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderProtocolError(
            f"model returned invalid JSON tool arguments: {text!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderProtocolError(
            f"model returned non-object tool arguments: {text!r}"
        )
    return parsed


def extract_model_ids(data: dict[str, Any] | None) -> list[str]:
    """Extract model ids from a ``{"data": [{"id": ...} | "..."]}`` body."""
    if not data:
        return []
    ids: list[str] = []
    for entry in data.get("data") or []:
        if isinstance(entry, str):
            ids.append(entry)
        elif isinstance(entry, dict) and entry.get("id"):
            ids.append(str(entry["id"]))
    return ids


class ChatProvider(ABC):
    """Base class for streaming chat providers.

    Subclasses implement :meth:`stream`, :meth:`list_models`, and the
    request-path helpers. A single :class:`httpx2.AsyncClient` is created
    lazily and reused for the provider's lifetime; call :meth:`aclose`
    when done.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._client: httpx2.AsyncClient | None = None

    @property
    def dialect(self) -> Dialect:
        """Which wire dialect this provider speaks."""
        return self.config.effective_dialect

    @property
    def _http(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(
                timeout=httpx2.Timeout(self.config.timeout)
            )
        return self._client

    async def aclose(self) -> None:
        """Release the underlying HTTP client, if created."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "text/event-stream",
        }
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.extra_headers)
        return headers

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema] | None = None,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one model turn over ``messages``, yielding provider events."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """List model identifiers advertised by the endpoint (best effort)."""

    async def _check_response(self, response: httpx2.Response) -> None:
        """Map a non-2xx response to a typed :class:`ProviderError`."""
        if response.is_success:
            return
        detail = ""
        with contextlib.suppress(httpx2.StreamError):
            detail = (await response.aread()).decode("utf-8", "replace")[:500]
        if response.status_code in (401, 403):
            from supex_driver.agent.errors import ProviderAuthError

            raise ProviderAuthError(
                f"provider rejected credentials (HTTP {response.status_code}): {detail}"
            )
        if response.status_code == 429:
            from supex_driver.agent.errors import ProviderRateLimitError

            raise ProviderRateLimitError(f"provider rate-limited (HTTP 429): {detail}")
        raise ProviderHTTPError(response.status_code, detail or response.reason_phrase)

    @asynccontextmanager
    async def _stream_text(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> AsyncIterator[httpx2.Response]:
        """Open a POST SSE stream; raise typed errors on connection failure."""
        try:
            async with self._http.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                await self._check_response(response)
                yield response
        except httpx2.ConnectTimeout as exc:
            raise ProviderTimeoutError(f"timed out connecting to {url}: {exc}") from exc
        except httpx2.TimeoutException as exc:
            raise ProviderTimeoutError(f"timed out talking to {url}: {exc}") from exc
        except httpx2.ConnectError as exc:
            raise ProviderConnectionError(f"could not reach {url}: {exc}") from exc
        except httpx2.HTTPError as exc:
            raise ProviderConnectionError(
                f"HTTP error talking to {url}: {exc}"
            ) from exc

    async def _get_json(
        self, url: str, headers: dict[str, str]
    ) -> dict[str, Any] | None:
        """Perform a GET and return parsed JSON, or None on 404/405."""
        try:
            async with self._http.stream("GET", url, headers=headers) as response:
                if response.status_code in (404, 405):
                    return None
                await self._check_response(response)
                body = await response.aread()
        except httpx2.TimeoutException as exc:
            raise ProviderTimeoutError(f"timed out talking to {url}: {exc}") from exc
        except httpx2.HTTPError as exc:
            raise ProviderConnectionError(f"could not reach {url}: {exc}") from exc
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderProtocolError(f"non-JSON response from {url}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ProviderProtocolError(f"unexpected response shape from {url}")
        return parsed

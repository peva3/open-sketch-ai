"""Local HTTP server hosting the supex-chat ``Agent`` for the windowed app.

Runs the shared :class:`~supex_driver.agent.agent.Agent` facade behind a
loopback-only HTTP server (stdlib :mod:`http.server`, no framework). The
agent's async provider and MCP client are bound to one dedicated asyncio
event loop that lives for the server's lifetime; every request marshals its
coroutine onto that loop, so streaming works and the HTTP/HTTPS clients are
never shared across threads.

Wire protocol (newline-delimited JSON, ``application/x-ndjson``)::

    {"type":"delta","text":"..."}                        # assistant text fragment
    {"type":"tool_call","id":...,"name":...,"arguments":"..."}
    {"type":"model_end","stop_reason":"...","usage":{...}|null}
    {"type":"tool_result","id":...,"name":...,"arguments":{...},"result":"...",
     "images":[{"media_type":...,"data":...}]}           # only when vision fed them back
    {"type":"done","text":"...","iterations":N,"tool_calls":N,
     "stopped":"end_turn"|"tool_limit","usage":{...}|null}   # terminal
    {"type":"error","error":"...","error_type":"..."}        # terminal

``done`` and ``error`` are always the final line of a stream. Endpoints:
``GET /`` (chat UI), ``GET /api/health``, ``GET /api/models``,
``POST /api/chat`` (one streamed user turn; body ``{"text": ..., "images"?: [...]}``),
``POST /api/reset``. Bind host/port come from ``SUPEX_AI_HOST``/
``SUPEX_AI_PORT`` (defaults ``127.0.0.1``/``8765``) or explicit arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
from collections.abc import Sequence
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from supex_driver.agent.agent import Agent
from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.providers.base import (
    ChatProvider,
    Done,
    ImagePart,
    ProviderEvent,
    TextDelta,
    ToolCallEvent,
)

logger = logging.getLogger("supex.agent.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_UI_DIR = Path(__file__).resolve().parent / "chat_ui"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _json_line(payload: dict[str, Any]) -> str:
    """Serialize a single NDJSON line (JSON has no raw newlines)."""
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _usage_dict(usage: Any) -> dict[str, int | None] | None:
    if usage is None:
        return None
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _image_dicts(images: Sequence[ImagePart]) -> list[dict[str, str]]:
    return [{"media_type": image.media_type, "data": image.data} for image in images]


class AgentServer:
    """Own an :class:`Agent` and expose it over loopback HTTP.

    One chat request runs at a time (single-user windowed app); concurrent
    ``/api/chat`` requests get HTTP 409. The agent is constructed by this
    server with its event callbacks routed into the active request's queue,
    so the terminal CLI and this server observe identical activity.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        provider: ChatProvider | None = None,
        workspace: str | Path | None = None,
        allow_delete: bool = False,
        command: Sequence[str] | None = None,
        read_timeout_seconds: float | None = None,
        host: str | None = None,
        port: int | None = None,
        ui_dir: str | Path | None = None,
    ) -> None:
        self._config = config
        self._host = host or os.environ.get("SUPEX_AI_HOST") or DEFAULT_HOST
        self._port = int(os.environ.get("SUPEX_AI_PORT") or port or DEFAULT_PORT)
        self._ui_dir = Path(ui_dir) if ui_dir is not None else _UI_DIR
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] | None = None
        self._agent = Agent(
            config=config,
            provider=provider,
            workspace=workspace,
            allow_delete=allow_delete,
            command=command,
            read_timeout_seconds=read_timeout_seconds,
            on_event=self._on_event,
            on_tool_result=self._on_tool_result,
        )

    # -- event bridge (runs on the background asyncio thread) -------------

    def _on_event(self, event: ProviderEvent) -> None:
        """Translate a provider event into a queued NDJSON line."""
        q = self._queue
        if q is None:
            return
        if isinstance(event, TextDelta):
            q.put(_json_line({"type": "delta", "text": event.text}))
        elif isinstance(event, ToolCallEvent):
            q.put(
                _json_line(
                    {
                        "type": "tool_call",
                        "id": event.id,
                        "name": event.name,
                        "arguments": event.arguments,
                    }
                )
            )
        elif isinstance(event, Done):
            q.put(
                _json_line(
                    {
                        "type": "model_end",
                        "stop_reason": event.stop_reason,
                        "usage": _usage_dict(event.usage),
                    }
                )
            )

    def _on_tool_result(
        self,
        tool_id: str,
        name: str,
        arguments: dict[str, Any],
        result: str,
        images: Sequence[ImagePart],
    ) -> None:
        q = self._queue
        if q is None:
            return
        q.put(
            _json_line(
                {
                    "type": "tool_result",
                    "id": tool_id,
                    "name": name,
                    "arguments": arguments,
                    "result": result,
                    "images": _image_dicts(images),
                }
            )
        )

    # -- lifecycle --------------------------------------------------------

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}"

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> None:
        """Start the background asyncio thread and the HTTP server."""
        if self._httpd is not None:
            return
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="supex-chat-loop", daemon=True
        )
        self._loop_thread.start()
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        self._httpd.daemon_threads = True
        thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="supex-chat-http",
            daemon=True,
        )
        thread.start()
        logger.info("supex-chat agent server listening on %s", self.url)

    def stop(self) -> None:
        """Shut the HTTP server and background event loop down."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5)
            self._loop_thread = None
        self._loop = None

    # -- request helpers (run on HTTP handler threads) ---------------------

    def _run_async(self, coro: Any) -> Any:
        """Run an awaitable on the background loop and block for its result."""
        assert self._loop is not None
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    # -- route implementations --------------------------------------------

    def _health(self) -> dict[str, Any]:
        cfg = self._config
        return {
            "ok": True,
            "model": cfg.model,
            "dialect": cfg.effective_dialect,
            "base_url": cfg.base_url,
            "vision": cfg.vision,
            "workspace": str(self._agent.files.workspace),
            "busy": self._lock.locked(),
        }

    def _list_models(self) -> dict[str, Any]:
        provider = self._agent.provider
        try:
            ids = self._run_async(provider.list_models())
        except Exception as exc:  # surfaced to the UI, not swallowed silently
            logger.warning("model listing failed: %s", exc)
            return {"models": [], "error": str(exc)}
        return {"models": ids}

    def _reset(self) -> dict[str, Any]:
        self._agent.reset_conversation()
        return {"ok": True}

    def _start_chat(
        self, text: str, images: Sequence[ImagePart]
    ) -> tuple[queue.Queue[str], Future]:
        """Begin one agent turn on the background loop.

        Returns ``(event_queue, future)``. The agent's event callbacks push
        NDJSON lines onto ``event_queue`` from the background asyncio thread
        as the turn progresses; the HTTP handler drains it in real time and
        awaits ``future`` for the final :class:`TurnResult`.
        """
        assert self._loop is not None
        q: queue.Queue[str] = queue.Queue()
        self._queue = q
        future = asyncio.run_coroutine_threadsafe(
            self._agent.run_turn(text, images=images or None), self._loop
        )
        return q, future

    def _finish_chat(self) -> None:
        self._queue = None

    def _parse_chat_payload(
        self, payload: dict[str, Any]
    ) -> tuple[str, list[ImagePart]]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("body must include a non-empty 'text' string")
        images: list[ImagePart] = []
        for item in payload.get("images") or []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("media_type"), str)
                and isinstance(item.get("data"), str)
            ):
                images.append(
                    ImagePart(media_type=item["media_type"], data=item["data"])
                )
        return text, images


def _make_handler(server: AgentServer) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to a specific ``AgentServer``."""

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "supex-chat"

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("http: " + format, *args)

        # -- helpers ------------------------------------------------------

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("JSON body must be an object")
            return parsed

        # -- routes -------------------------------------------------------

        def do_GET(self) -> None:
            if self.path == "/api/health":
                self._send_json(200, server._health())
                return
            if self.path == "/api/models":
                self._send_json(200, server._list_models())
                return
            self._serve_static(self.path)

        def do_POST(self) -> None:
            if self.path == "/api/reset":
                self._send_json(200, server._reset())
                return
            if self.path == "/api/chat":
                self._do_chat()
                return
            self._send_json(404, {"ok": False, "error": f"no route {self.path}"})

        def _do_chat(self) -> None:
            if not server._lock.acquire(blocking=False):
                self._send_json(
                    409, {"ok": False, "error": "a turn is already running"}
                )
                return
            try:
                payload = self._read_json()
            except ValueError as exc:
                server._lock.release()
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            try:
                text, images = server._parse_chat_payload(payload)
            except ValueError as exc:
                server._lock.release()
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            error: tuple[str, str] | None = None
            result: Any = None
            try:
                q, future = server._start_chat(text, images)
                while True:
                    try:
                        line = q.get(timeout=0.25)
                    except queue.Empty:
                        if future.done():
                            break
                        continue
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                while True:
                    try:
                        line = q.get_nowait()
                    except queue.Empty:
                        break
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                try:
                    result = future.result()
                except Exception as exc:  # surfaced as a terminal error line
                    logger.exception("agent turn raised")
                    error = (str(exc), type(exc).__name__)
            finally:
                server._finish_chat()
                server._lock.release()
            if error is not None:
                terminal = _json_line(
                    {"type": "error", "error": error[0], "error_type": error[1]}
                )
            else:
                terminal = _json_line(
                    {
                        "type": "done",
                        "text": result.text,
                        "iterations": result.iterations,
                        "tool_calls": result.tool_calls,
                        "stopped": result.stopped,
                        "usage": _usage_dict(result.usage),
                    }
                )
            self.wfile.write(terminal.encode("utf-8"))
            self.wfile.flush()
            self.close_connection = True

        def _serve_static(self, path: str) -> None:
            relative = path.lstrip("/").split("?", 1)[0] or "index.html"
            candidate = (server._ui_dir / relative).resolve()
            try:
                inside = candidate.is_relative_to(server._ui_dir.resolve())
            except (ValueError, OSError):
                inside = False
            if not inside or not candidate.is_file():
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            media = _CONTENT_TYPES.get(candidate.suffix.lower())
            if media is None:
                self._send_json(
                    404,
                    {"ok": False, "error": f"unsupported file type {candidate.suffix}"},
                )
                return
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return _Handler

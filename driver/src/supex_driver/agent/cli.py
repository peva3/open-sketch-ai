"""supex-chat: terminal client for the provider-agnostic SketchUp agent.

The canonical, cross-platform console entry is ``supex-chat`` (see
``driver/pyproject.toml``). ``./sketch`` at the repo root is a Unix-only
convenience wrapper around this same command.

Two interfaces share one :class:`Agent` core:

* ``supex-chat``            interactive session (slash commands)
* ``supex-chat serve``      launch the Phase 4 windowed chat app
* ``supex-chat -p "…"``     one-shot non-interactive turn
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import webbrowser
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape as markup_escape

from supex_driver.agent import load_config
from supex_driver.agent.agent import Agent
from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import AgentError, ConfigError
from supex_driver.agent.profiles import resolve_profile
from supex_driver.agent.providers.base import (
    Done,
    ProviderEvent,
    TextDelta,
    ToolCallEvent,
    Usage,
)
from supex_driver.agent.server import AgentServer

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    _VERSION = _pkg_version("supex-driver")
except PackageNotFoundError:
    _VERSION = "0.0.0"

app = typer.Typer(
    name="supex-chat",
    help="Provider-agnostic AI agent for SketchUp (terminal or windowed chat).",
    no_args_is_help=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

_logging_configured = False


def _ensure_logging() -> None:
    """Configure file logging once (mirrors ``cli/main.py``).

    Writes to ``SUPEX_LOG_DIR`` (default ``$SUPEX_WORKSPACE/.tmp/logs``),
    file ``agent-chat.log``. Degrades to a null handler if the dir is
    unavailable so a missing log path never crashes the CLI.
    """
    global _logging_configured
    if _logging_configured:
        return
    workspace = os.environ.get(
        "SUPEX_WORKSPACE", os.path.expanduser("~/.supex/tmp-workspace")
    )
    log_dir = os.environ.get("SUPEX_LOG_DIR", os.path.join(workspace, ".tmp", "logs"))
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(os.path.join(log_dir, "agent-chat.log"), mode="a")
        formatter = logging.Formatter(
            fmt="%(asctime)s|%(levelname)s|%(name)s|%(message)s",
        )
        formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
        formatter.default_msec_format = "%s.%03d"
        handler.setFormatter(formatter)
        logging.root.addHandler(handler)
        logging.root.setLevel(logging.DEBUG)
    except OSError:
        logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()])
    _logging_configured = True


# -- config option aliases shared by the interactive command and ``serve`` -----

ModelOpt = Annotated[
    str | None, typer.Option("--model", "-m", help="Model id (e.g. claude-…, gpt-…).")
]
BaseUrlOpt = Annotated[
    str | None, typer.Option("--base-url", help="Provider base URL (dialect sniffed).")
]
ApiKeyOpt = Annotated[
    str | None, typer.Option("--api-key", help="Provider API key (never logged).")
]
DialectOpt = Annotated[
    str,
    typer.Option(
        "--dialect",
        help="API dialect: auto, openai, or anthropic.",
    ),
]
VisionOpt = Annotated[
    bool | None,
    typer.Option("--vision/--no-vision", help="Attach screenshots to model turns."),
]
AllowDeleteOpt = Annotated[
    bool, typer.Option("--allow-delete", help="Enable the delete_file tool.")
]
TimeoutOpt = Annotated[
    float | None, typer.Option("--timeout", help="Provider request timeout seconds.")
]
TemperatureOpt = Annotated[
    float | None, typer.Option("--temperature", help="Sampling temperature.")
]
MaxTokensOpt = Annotated[
    int | None, typer.Option("--max-tokens", help="Max output tokens per stream.")
]
MaxIterOpt = Annotated[
    int | None, typer.Option("--max-iterations", help="Max tool iterations per turn.")
]
ProviderProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--provider-profile",
        help="Named profile from the user profiles.toml (see --help for location).",
    ),
]


def _config_kwargs(
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    dialect: str,
    vision: bool | None,
    timeout: float | None,
    temperature: float | None,
    max_tokens: int | None,
    max_iterations: int | None,
) -> dict[str, Any]:
    """Collect resolved config knobs, guarding against unknown dialects."""
    if dialect not in ("auto", "openai", "anthropic"):
        raise typer.BadParameter(
            f"--dialect must be auto, openai, or anthropic (got {dialect!r})"
        )
    return {
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "dialect": dialect,
        "vision": vision,
        "timeout": timeout,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_iterations": max_iterations,
    }


class EventPrinter:
    """Stream model events to the terminal as they arrive.

    One instance is handed to :class:`Agent` as ``on_event`` so the terminal
    shows exactly the activity the windowed app renders. Plain-text output
    (``SUPEX_PLAIN``/``NO_COLOR``/non-TTY) writes to ``sys.stdout`` without
    decoration; rich output is reserved for the tool/status banners.
    """

    def __init__(self, *, plain: bool = False) -> None:
        self._plain = plain
        self._console: Console | None = None if plain else Console(width=200)
        self._streamed_text = False

    @property
    def streamed_text(self) -> bool:
        """True once a text delta has been written (avoid double final echo)."""
        return self._streamed_text

    def _write(self, text: str) -> None:
        if self._plain:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            assert self._console is not None
            self._console.print(text, markup=False, end="", soft_wrap=True)

    def newline(self) -> None:
        self._write("\n")

    def banner(self, text: str, *, style: str = "dim") -> None:
        if self._plain:
            print(text)
        else:
            assert self._console is not None
            self._console.print(f"[{style}]{markup_escape(text)}[/{style}]")

    def on_event(self, event: ProviderEvent) -> None:
        if isinstance(event, TextDelta):
            self._write(event.text)
            self._streamed_text = True
        elif isinstance(event, ToolCallEvent):
            self.newline()
            self.banner(
                f"tool call {event.id}: {event.name}({event.arguments})",
                style="yellow",
            )
        elif isinstance(event, Done):
            if event.stop_reason == "tool_use":
                pass
            elif event.usage is not None:
                self.newline()
                self.banner(_usage_text(event.usage), style="dim")


def _usage_text(usage: Usage) -> str:
    parts = []
    if usage.input_tokens is not None:
        parts.append(f"{usage.input_tokens} in")
    if usage.output_tokens is not None:
        parts.append(f"{usage.output_tokens} out")
    if usage.total_tokens is not None:
        parts.append(f"{usage.total_tokens} total")
    return "usage: " + ", ".join(parts)


async def _slash_command(agent: Agent, printer: EventPrinter, cmd: str) -> bool:
    """Handle one slash command. Returns True if it was a slash command.

    Supported: /help /model /tools /status /reset /exit. Unknown commands
    print a hint. Pure enough to unit test with a stubbed agent.
    """
    if not cmd.startswith("/"):
        return False
    name = cmd.split(None, 1)[0].lower()
    if name == "/help":
        printer.banner(_HELP_TEXT, style="cyan")
    elif name == "/model":
        cfg = agent.config
        printer.banner(
            f"model: {cfg.model}  dialect: {cfg.effective_dialect}\n"
            f"base_url: {cfg.base_url}  vision: {cfg.vision}",
            style="cyan",
        )
    elif name == "/tools":
        tools = await agent.tools()
        for tool in tools:
            printer.banner(f"  {tool.name} — {tool.description}", style="cyan")
        printer.banner(f"{len(tools)} tools available", style="dim")
    elif name == "/status":
        status = await agent.backend_status()
        lines = [
            f"backend tools: {status['tools']}",
            f"check_status: {status['check_status']}",
        ]
        usage = agent.total_usage
        messages = len(agent.history)
        if usage is not None:
            lines.append(_usage_text(usage))
        lines.append(f"messages in session: {messages}")
        printer.banner("\n".join(lines), style="cyan")
    elif name == "/reset":
        agent.reset_conversation()
        printer.banner("conversation reset", style="green")
    elif name == "/exit":
        raise SystemExit(0)
    else:
        printer.banner(f"unknown command {name!r} — try /help", style="yellow")
    return True


async def _run_one_turn(agent: Agent, text: str, printer: EventPrinter) -> None:
    """Execute a single user turn, then print the final assistant text."""
    try:
        result = await agent.run_turn(text)
    except AgentError as exc:
        printer.newline()
        printer.banner(f"error: {exc}", style="red")
        return
    if not printer.streamed_text and result.text:
        print(result.text)
    printer.newline()
    printer.banner(
        f"[{result.stopped}] iterations={result.iterations} "
        f"tool_calls={result.tool_calls}",
        style="dim",
    )


async def _interactive_async(
    config: ProviderConfig,
    printer: EventPrinter,
    *,
    allow_delete: bool = False,
) -> None:
    """Interactive REPL: read prompt lines and drive the Agent to completion."""
    agent = Agent(
        config=config,
        allow_delete=allow_delete,
        on_event=printer.on_event,
    )
    try:
        printer.banner(
            f"supex-chat {_VERSION} — model {config.model} "
            f"[{config.effective_dialect}]. /help for commands, /exit to quit.",
            style="cyan",
        )
        while True:
            try:
                line = input("> ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                break
            text = line.strip()
            if not text:
                continue
            if text.startswith("/"):
                try:
                    handled = await _slash_command(agent, printer, text)
                    if not handled:
                        print(text)
                except SystemExit:
                    break
                continue
            try:
                await _run_one_turn(agent, text, printer)
            except KeyboardInterrupt:
                printer.newline()
                printer.banner("interrupted (type /exit to quit)", style="yellow")
    finally:
        await agent.aclose()


@app.callback()
def chat(
    ctx: typer.Context,
    prompt: Annotated[
        str | None, typer.Option("--prompt", "-p", help="One-shot prompt, then exit.")
    ] = None,
    list_models: Annotated[
        bool, typer.Option("--list-models", help="List provider models, then exit.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Check backend connectivity, then exit.")
    ] = False,
    version: Annotated[
        bool, typer.Option("--version", help="Print version, then exit.")
    ] = False,
    model: ModelOpt = None,
    base_url: BaseUrlOpt = None,
    api_key: ApiKeyOpt = None,
    dialect: DialectOpt = "auto",
    vision: VisionOpt = None,
    allow_delete: AllowDeleteOpt = False,
    timeout: TimeoutOpt = None,
    temperature: TemperatureOpt = None,
    max_tokens: MaxTokensOpt = None,
    max_iterations: MaxIterOpt = None,
    provider_profile: ProviderProfileOpt = None,
) -> None:
    """Run the agent in the terminal (interactive by default)."""
    if ctx.invoked_subcommand is not None:
        return
    if version:
        print(f"supex-chat {_VERSION}")
        raise typer.Exit()
    _ensure_logging()
    printer = EventPrinter()
    config = _load_config_from(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dialect=dialect,
        vision=vision,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        provider_profile=provider_profile,
    )
    if list_models:
        asyncio.run(_print_models(config, printer))
        raise typer.Exit()
    if check:
        asyncio.run(_run_check(config, printer))
        raise typer.Exit()
    if prompt is not None:
        asyncio.run(_one_shot(config, prompt, printer, allow_delete=allow_delete))
        raise typer.Exit()
    asyncio.run(_interactive_async(config, printer, allow_delete=allow_delete))


@app.command("serve")
def serve(
    model: ModelOpt = None,
    base_url: BaseUrlOpt = None,
    api_key: ApiKeyOpt = None,
    dialect: DialectOpt = "auto",
    vision: VisionOpt = None,
    allow_delete: AllowDeleteOpt = False,
    timeout: TimeoutOpt = None,
    temperature: TemperatureOpt = None,
    max_tokens: MaxTokensOpt = None,
    max_iterations: MaxIterOpt = None,
    provider_profile: ProviderProfileOpt = None,
    host: Annotated[
        str, typer.Option("--host", help="Bind host (loopback default).")
    ] = "127.0.0.1",
    port: Annotated[
        int, typer.Option("--port", help="Bind port (SUPEX_AI_PORT or 8765).")
    ] = 0,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Do not open a browser tab.")
    ] = False,
) -> None:
    """Launch the windowed chat app (local server + browser)."""
    _ensure_logging()
    printer = EventPrinter()
    config = _load_config_from(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dialect=dialect,
        vision=vision,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        provider_profile=provider_profile,
    )
    server = AgentServer(
        config=config,
        allow_delete=allow_delete,
        host=host,
        port=port or None,
    )
    server.start()
    printer.banner(f"supex-chat windowed app: {server.url}", style="green")
    printer.banner("press Ctrl-C to stop the server", style="dim")
    if not no_open:
        webbrowser.open(server.url)
    try:
        while True:
            try:
                time.sleep(3600)
            except KeyboardInterrupt:
                break
    finally:
        server.stop()


def _load_config_from(
    *,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
    dialect: str,
    vision: bool | None,
    timeout: float | None,
    temperature: float | None,
    max_tokens: int | None,
    max_iterations: int | None,
    provider_profile: str | None = None,
) -> ProviderConfig:
    kwargs = _config_kwargs(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dialect=dialect,
        vision=vision,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
    )
    try:
        profile = resolve_profile(
            provider_profile or os.environ.get("SUPEX_AI_PROFILE")
        )
        return load_config(profile=profile, **kwargs)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc


async def _print_models(config: ProviderConfig, printer: EventPrinter) -> None:
    from supex_driver.agent.providers import build_provider

    provider = build_provider(config)
    try:
        models = await provider.list_models()
    except AgentError as exc:
        printer.banner(f"could not list models: {exc}", style="red")
        return
    finally:
        await provider.aclose()
    for model_id in models:
        print(model_id)
    if not models:
        printer.banner("no models reported by the provider", style="yellow")


async def _run_check(config: ProviderConfig, printer: EventPrinter) -> None:
    """Verify provider + SketchUp backend connectivity (``supex-chat --check``)."""
    from supex_driver.agent.providers import build_provider

    provider = build_provider(config)
    try:
        await provider.list_models()
        printer.banner(f"provider reachable: {config.base_url}", style="green")
    except AgentError as exc:
        printer.banner(f"provider unreachable: {exc}", style="red")
    finally:
        await provider.aclose()
    agent = Agent(config=config, allow_delete=False)
    try:
        status = await agent.backend_status()
        printer.banner(
            f"MCP backend: {status['tools']} tools — {status['check_status']}",
            style="green" if "error" not in str(status["check_status"]) else "yellow",
        )
    except AgentError as exc:
        printer.banner(f"MCP backend unreachable: {exc}", style="red")
    finally:
        await agent.aclose()


async def _one_shot(
    config: ProviderConfig,
    prompt: str,
    printer: EventPrinter,
    *,
    allow_delete: bool = False,
) -> None:
    agent = Agent(
        config=config,
        allow_delete=allow_delete,
        on_event=printer.on_event,
    )
    try:
        await _run_one_turn(agent, prompt, printer)
    finally:
        await agent.aclose()


_HELP_TEXT = (
    "Commands:\n"
    "  /help    show this help\n"
    "  /model   show the active provider model and dialect\n"
    "  /tools   list the tools the agent can call\n"
    "  /status  ping the SketchUp backend (check_status)\n"
    "  /reset   forget conversation history\n"
    "  /exit    quit\n"
    "\n"
    "Type a prompt to run the agent. The model may author .rb files and call\n"
    "eval_ruby_file to apply changes live inside SketchUp, then screenshot its\n"
    "own output. Press Ctrl-C during a turn to interrupt it."
)


def main() -> None:
    """Console entry point (``supex-chat = supex_driver.agent.cli:main``)."""
    app()


if __name__ == "__main__":
    main()

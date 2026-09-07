"""Tests for the supex-chat CLI (agent/cli.py) — no live I/O.

Covers option/config resolution, the slash-command router, one-shot turn
output, and help/version plumbing. Provider and SketchUp backends are
scripted (ScriptedProvider / FakeBackend), so nothing touches the network.
"""

import pytest
import typer

from supex_driver.agent import agent as agent_mod
from supex_driver.agent import cli as cli_mod
from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import AgentError
from supex_driver.agent.providers.base import Done, TextDelta, ToolSchema
from tests.agent.fakes import FakeBackend, ScriptedProvider

ENV_KEYS = [
    "SUPEX_AI_BASE_URL",
    "SUPEX_AI_API_KEY",
    "SUPEX_AI_MODEL",
    "SUPEX_AI_DIALECT",
    "SUPEX_AI_TIMEOUT",
    "SUPEX_AI_TEMPERATURE",
    "SUPEX_AI_MAX_TOKENS",
    "SUPEX_AI_MAX_ITERATIONS",
    "SUPEX_AI_VISION",
    "SUPEX_AI_PROFILE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _config(**over) -> ProviderConfig:
    defaults = {
        "base_url": "http://localhost:1",
        "api_key": "test-key",
        "model": "test-model",
        "dialect": "openai",
    }
    defaults.update(over)
    return ProviderConfig(**defaults)


@pytest.fixture
def plain_printer():
    return cli_mod.EventPrinter(plain=True)


class TestConfigKwargs:
    def test_collects_all_knobs(self):
        kwargs = cli_mod._config_kwargs(
            model="m",
            base_url="http://x",
            api_key="k",
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=0.5,
            max_tokens=1024,
            max_iterations=7,
        )
        assert kwargs == {
            "model": "m",
            "base_url": "http://x",
            "api_key": "k",
            "dialect": "auto",
            "vision": None,
            "timeout": None,
            "temperature": 0.5,
            "max_tokens": 1024,
            "max_iterations": 7,
        }

    @pytest.mark.parametrize("bad", ["gpt-ish", "", "ANTHROPIC"])
    def test_invalid_dialect_rejected(self, bad):
        with pytest.raises(typer.BadParameter):
            cli_mod._config_kwargs(
                model="m",
                base_url=None,
                api_key=None,
                dialect=bad,
                vision=None,
                timeout=None,
                temperature=None,
                max_tokens=None,
                max_iterations=None,
            )


class TestLoadConfigFrom:
    def test_resolves_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        monkeypatch.setenv("OPENAI_MODEL", "env-model")
        cfg = cli_mod._load_config_from(
            model=None,
            base_url=None,
            api_key=None,
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=None,
            max_tokens=None,
            max_iterations=None,
        )
        assert cfg.model == "env-model"
        assert cfg.api_key == "sk-env"

    def test_flags_beat_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        monkeypatch.setenv("OPENAI_MODEL", "env-model")
        cfg = cli_mod._load_config_from(
            model="flag-model",
            base_url=None,
            api_key="sk-flag",
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=None,
            max_tokens=None,
            max_iterations=None,
        )
        assert cfg.model == "flag-model"
        assert cfg.api_key == "sk-flag"

    def test_missing_model_exits_2(self):
        with pytest.raises(typer.Exit) as exc:
            cli_mod._load_config_from(
                model=None,
                base_url="http://localhost:8000",
                api_key=None,
                dialect="auto",
                vision=None,
                timeout=None,
                temperature=None,
                max_tokens=None,
                max_iterations=None,
            )
        assert exc.value.exit_code == 2

    def test_provider_profile_fills_config(self, monkeypatch):
        profile = {
            "base_url": "http://localhost:8000",
            "api_key": "sk-unsloth-test",
            "model": "qwen3-local",
            "dialect": "anthropic",
        }
        monkeypatch.setattr(cli_mod, "resolve_profile", lambda name: profile)
        cfg = cli_mod._load_config_from(
            model=None,
            base_url=None,
            api_key=None,
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=None,
            max_tokens=None,
            max_iterations=None,
            provider_profile="unsloth",
        )
        assert cfg.model == "qwen3-local"
        assert cfg.base_url == "http://localhost:8000"
        assert cfg.dialect == "anthropic"

    def test_supex_ai_profile_env_selects_profile(self, monkeypatch):
        profile = {"base_url": "http://localhost:9000", "model": "m-from-profile"}
        monkeypatch.setattr(cli_mod, "resolve_profile", lambda name: profile)
        monkeypatch.setenv("SUPEX_AI_PROFILE", "local")
        cfg = cli_mod._load_config_from(
            model=None,
            base_url=None,
            api_key=None,
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=None,
            max_tokens=None,
            max_iterations=None,
        )
        assert cfg.base_url == "http://localhost:9000"

    def test_flags_beat_provider_profile(self, monkeypatch):
        profile = {"base_url": "http://localhost:8000", "model": "qwen3-local"}
        monkeypatch.setattr(cli_mod, "resolve_profile", lambda name: profile)
        cfg = cli_mod._load_config_from(
            model="flag-model",
            base_url=None,
            api_key=None,
            dialect="auto",
            vision=None,
            timeout=None,
            temperature=None,
            max_tokens=None,
            max_iterations=None,
            provider_profile="unsloth",
        )
        assert cfg.model == "flag-model"

    def test_unknown_profile_exits_2(self, monkeypatch, capsys):
        from supex_driver.agent.errors import ConfigError

        def _boom(name):
            raise ConfigError("unknown provider profile 'nope'")

        monkeypatch.setattr(cli_mod, "resolve_profile", _boom)
        with pytest.raises(typer.Exit) as exc:
            cli_mod._load_config_from(
                model=None,
                base_url=None,
                api_key=None,
                dialect="auto",
                vision=None,
                timeout=None,
                temperature=None,
                max_tokens=None,
                max_iterations=None,
                provider_profile="nope",
            )
        assert exc.value.exit_code == 2
        assert "unknown provider profile" in capsys.readouterr().err


class TestEventPrinter:
    def test_text_delta_streams_and_flags(self, plain_printer, capsys):
        plain_printer.on_event(TextDelta("hello "))
        plain_printer.on_event(TextDelta("world"))
        assert capsys.readouterr().out == "hello world"
        assert plain_printer.streamed_text is True

    def test_tool_banner_written_plain(self, plain_printer, capsys):
        from supex_driver.agent.providers.base import ToolCallEvent

        plain_printer.on_event(ToolCallEvent("c1", "eval_ruby", "{}"))
        out = capsys.readouterr().out
        assert "tool call c1: eval_ruby({})" in out

    def test_done_usage_printed(self, plain_printer, capsys):
        from supex_driver.agent.providers.base import Usage

        plain_printer.on_event(Done("end_turn", usage=Usage(1, 2, 3)))
        out = capsys.readouterr().out
        assert "usage:" in out and "1 in" in out and "3 total" in out

    def test_plain_disables_rich(self):
        printer = cli_mod.EventPrinter(plain=True)
        assert printer._console is None


class TestSlashRouting:
    async def test_non_slash_returns_false(self, plain_printer):
        agent = object()  # type: ignore[arg-type]
        assert await cli_mod._slash_command(agent, plain_printer, "hello") is False

    async def test_help_prints_help(self, plain_printer, capsys):
        agent = object()  # type: ignore[arg-type]
        handled = await cli_mod._slash_command(agent, plain_printer, "/help")
        assert handled is True
        assert "/status" in capsys.readouterr().out

    async def test_unknown_command_prints_hint(self, plain_printer, capsys):
        agent = object()  # type: ignore[arg-type]
        handled = await cli_mod._slash_command(agent, plain_printer, "/bogus")
        assert handled is True
        assert "unknown command" in capsys.readouterr().out

    async def test_exit_raises_system_exit(self, plain_printer):
        agent = object()  # type: ignore[arg-type]
        with pytest.raises(SystemExit) as exc:
            await cli_mod._slash_command(agent, plain_printer, "/exit")
        assert exc.value.code == 0


class TestSlashModelAndReset:
    def _backend(self):
        backend = FakeBackend(
            [
                ToolSchema(name="check_status", description="health"),
                ToolSchema(name="eval_ruby", description="run ruby"),
            ]
        )
        return backend

    async def _agent(self, monkeypatch, config=None):
        backend = self._backend()
        monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: backend)
        agent = agent_mod.Agent(
            config=config or _config(),
            provider=ScriptedProvider(dialect="openai"),
        )
        return agent, backend

    async def test_model_reports_config(self, monkeypatch, plain_printer, capsys):
        agent, _ = await self._agent(monkeypatch, config=_config(model="claude-x"))
        handled = await cli_mod._slash_command(agent, plain_printer, "/model")
        assert handled is True
        out = capsys.readouterr().out
        assert "claude-x" in out and "openai" in out

    async def test_tools_lists_backend_and_counts(
        self, monkeypatch, plain_printer, capsys
    ):
        agent, _ = await self._agent(monkeypatch)
        handled = await cli_mod._slash_command(agent, plain_printer, "/tools")
        assert handled is True
        out = capsys.readouterr().out
        assert "eval_ruby" in out and "check_status" in out
        # 2 backend tools + 5 file tools
        assert "7 tools available" in out

    async def test_status_calls_check_status(self, monkeypatch, plain_printer, capsys):
        agent, backend = await self._agent(monkeypatch)
        handled = await cli_mod._slash_command(agent, plain_printer, "/status")
        assert handled is True
        out = capsys.readouterr().out
        assert "backend tools: 7" in out
        assert backend.calls and backend.calls[0][0] == "check_status"
        await agent.aclose()

    async def test_reset_forgets_history(self, monkeypatch, plain_printer, capsys):
        agent, _ = await self._agent(monkeypatch)
        agent.reset_conversation()
        handled = await cli_mod._slash_command(agent, plain_printer, "/reset")
        assert handled is True
        assert "conversation reset" in capsys.readouterr().out
        assert agent.history == []
        await agent.aclose()


class TestOneShot:
    async def test_one_shot_prints_final_text_when_not_streamed(
        self, monkeypatch, plain_printer, capsys
    ):
        backend = FakeBackend([ToolSchema(name="eval_ruby", description="ruby")])
        monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: backend)
        agent = agent_mod.Agent(
            config=_config(),
            provider=ScriptedProvider(dialect="openai", turns=[[TextDelta("answer")]]),
        )
        await cli_mod._run_one_turn(agent, "hi", plain_printer)
        out = capsys.readouterr().out
        assert "answer" in out
        await agent.aclose()

    async def test_agent_error_reported_not_raised(
        self, monkeypatch, plain_printer, capsys
    ):
        class BoomProvider:
            dialect = "openai"

            async def stream(self, messages, tools=None, *, model=None, system=None):
                yield Done("end_turn")

            async def aclose(self):
                pass

            async def list_models(self):
                return []

        backend = FakeBackend([ToolSchema(name="eval_ruby", description="ruby")])
        monkeypatch.setattr(agent_mod, "SketchUpMCP", lambda **kw: backend)

        async def _raise(self, text, images=None):
            raise AgentError("boom")

        monkeypatch.setattr(agent_mod.Agent, "run_turn", _raise)
        agent = agent_mod.Agent(config=_config(), provider=BoomProvider())
        await cli_mod._run_one_turn(agent, "hi", plain_printer)
        out = capsys.readouterr().out
        assert "error: boom" in out
        await agent.aclose()

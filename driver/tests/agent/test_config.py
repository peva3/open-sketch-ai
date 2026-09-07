"""Tests for agent/provider configuration resolution (config.py)."""

import pytest

from supex_driver.agent.config import (
    _ANTHROPIC_DEFAULT_BASE,
    _OPENAI_DEFAULT_BASE,
    ProviderConfig,
    detect_dialect,
    load_config,
)
from supex_driver.agent.errors import ConfigError

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
    "SUPEX_AI_AUTH_STYLE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_agent_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _env(**overrides):
    env = {
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_API_KEY": "sk-openai-test",
        "OPENAI_MODEL": "gpt-test",
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "ANTHROPIC_MODEL": "claude-test",
    }
    env.update(overrides)
    return env


class TestDetectDialect:
    def test_forced_openai(self):
        assert detect_dialect("https://x.example", "sk-ant-abc", "openai") == "openai"

    def test_forced_anthropic(self):
        assert detect_dialect("https://x.example", "sk-x", "anthropic") == "anthropic"

    def test_auto_from_anthropic_url(self):
        assert detect_dialect("https://api.anthropic.com", "key", "auto") == "anthropic"

    def test_auto_from_messages_path(self):
        assert (
            detect_dialect("http://localhost:8000/v1/messages", "key", "auto")
            == "anthropic"
        )

    def test_auto_defaults_openai(self):
        assert detect_dialect("http://localhost:8000/v1", None, "auto") == "openai"

    def test_auto_from_anthropic_key(self):
        assert detect_dialect(None, "sk-ant-test123", "auto") == "anthropic"

    def test_auto_no_key_defaults_openai(self):
        assert detect_dialect(None, None, "auto") == "openai"


class TestLoadConfigPrecedence:
    def test_openai_env_group(self):
        cfg = load_config(env=_env())
        assert cfg.dialect == "openai"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.api_key == "sk-openai-test"
        assert cfg.model == "gpt-test"
        assert cfg.effective_dialect == "openai"

    def test_both_env_groups_default_to_openai(self):
        # With both standard groups present and no base URL or key to
        # disambiguate, the OpenAI group is the documented fallback.
        cfg = load_config(env=_env())
        assert cfg.dialect == "openai"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-test"

    def test_anthropic_only_env_group(self):
        env = {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "ANTHROPIC_MODEL": "claude-test",
        }
        cfg = load_config(env=env)
        assert cfg.dialect == "anthropic"
        assert cfg.base_url == "https://api.anthropic.com"
        assert cfg.api_key == "sk-ant-test"
        assert cfg.model == "claude-test"

    def test_anthropic_auth_token_fallback(self):
        env = {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "ANTHROPIC_AUTH_TOKEN": "secret-token",
            "ANTHROPIC_MODEL": "claude-test",
        }
        cfg = load_config(env=env)
        assert cfg.dialect == "anthropic"
        assert cfg.api_key == "secret-token"

    def test_supex_env_overrides_standard_group(self):
        cfg = load_config(
            env=_env(
                SUPEX_AI_BASE_URL="http://localhost:8000/v1",
                SUPEX_AI_API_KEY="sk-supex",
                SUPEX_AI_MODEL="local-model",
            )
        )
        assert cfg.base_url == "http://localhost:8000/v1"
        assert cfg.api_key == "sk-supex"
        assert cfg.model == "local-model"

    def test_explicit_kwargs_override_env(self):
        cfg = load_config(
            base_url="http://cli.example/v1",
            api_key="sk-cli",
            model="cli-model",
            env=_env(),
        )
        assert cfg.base_url == "http://cli.example/v1"
        assert cfg.api_key == "sk-cli"
        assert cfg.model == "cli-model"

    def test_explicit_dialect_wins_over_env(self):
        cfg = load_config(dialect="anthropic", env=_env())
        assert cfg.dialect == "anthropic"
        assert cfg.base_url == _ANTHROPIC_DEFAULT_BASE
        assert cfg.model == "claude-test"

    def test_missing_model_raises(self):
        with pytest.raises(ConfigError, match="no model configured"):
            load_config(env={"OPENAI_BASE_URL": "https://api.openai.com/v1"})


class TestLoadConfigScalars:
    def test_defaults_when_unset(self):
        cfg = load_config(env={"OPENAI_MODEL": "m"})
        assert cfg.timeout == 60.0
        assert cfg.temperature is None
        assert cfg.max_tokens is None
        assert cfg.max_iterations == 10
        assert cfg.vision is False
        assert cfg.auth_header == "auto"

    def test_temperature_parsed(self):
        cfg = load_config(env=_env(SUPEX_AI_TEMPERATURE="0.7"))
        assert cfg.temperature == pytest.approx(0.7)

    def test_invalid_temperature_raises(self):
        with pytest.raises(ConfigError, match="SUPEX_AI_TEMPERATURE"):
            load_config(env=_env(SUPEX_AI_TEMPERATURE="hot"))

    def test_max_tokens_parsed(self):
        cfg = load_config(env=_env(SUPEX_AI_MAX_TOKENS="2048"))
        assert cfg.max_tokens == 2048

    def test_max_tokens_zero_becomes_none(self):
        cfg = load_config(env=_env(SUPEX_AI_MAX_TOKENS="0"))
        assert cfg.max_tokens is None

    def test_max_iterations_parsed(self):
        cfg = load_config(env=_env(SUPEX_AI_MAX_ITERATIONS="25"))
        assert cfg.max_iterations == 25

    def test_vision_truthy(self):
        cfg = load_config(env=_env(SUPEX_AI_VISION="1"))
        assert cfg.vision is True

    def test_vision_falsy(self):
        cfg = load_config(env=_env(SUPEX_AI_VISION="no"))
        assert cfg.vision is False

    def test_auth_style_bearer(self):
        cfg = load_config(env=_env(SUPEX_AI_AUTH_STYLE="bearer"))
        assert cfg.auth_header == "bearer"

    def test_invalid_auth_style_defaults_auto(self):
        cfg = load_config(env=_env(SUPEX_AI_AUTH_STYLE="nope"))
        assert cfg.auth_header == "auto"

    def test_timeout_env_parsed(self):
        cfg = load_config(env=_env(SUPEX_AI_TIMEOUT="30"))
        assert cfg.timeout == 30.0


class TestProviderConfigProperties:
    def test_effective_dialect_matches_stored_concrete(self):
        cfg = load_config(env=_env())
        assert cfg.dialect == "openai"
        assert cfg.effective_dialect == "openai"

    def test_is_local_true_for_localhost(self):
        cfg = ProviderConfig(
            base_url="http://localhost:8000/v1", model="m", dialect="openai"
        )
        assert cfg.is_local is True

    def test_is_local_true_for_loopback_ip(self):
        cfg = ProviderConfig(
            base_url="http://127.0.0.1:8000/v1", model="m", dialect="openai"
        )
        assert cfg.is_local is True

    def test_is_local_false_for_cloud(self):
        cfg = ProviderConfig(
            base_url="https://api.openai.com/v1", model="m", dialect="openai"
        )
        assert cfg.is_local is False

    def test_default_base_openai(self):
        cfg = load_config(env={"OPENAI_MODEL": "m"})
        assert cfg.base_url == _OPENAI_DEFAULT_BASE

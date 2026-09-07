"""Tests for the provider-profiles file reader (profiles.py)."""

import pytest

from supex_driver.agent.errors import ConfigError
from supex_driver.agent.profiles import (
    config_dir,
    default_profile,
    load_profiles,
    profiles_path,
    resolve_profile,
)

UNSLOTH_TOML = """
default = "unsloth"

[profiles.openai]
base_url = "https://api.openai.com/v1"
model = "gpt-5"
dialect = "openai"

[profiles.unsloth]
base_url = "http://localhost:8000"
api_key = "sk-unsloth-test"
model = "qwen3-local"
dialect = "anthropic"
"""


class TestConfigDir:
    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUPEX_AI_CONFIG_DIR", str(tmp_path / "custom"))
        assert config_dir() == (tmp_path / "custom").resolve()

    def test_env_override_expands_tilde(self, monkeypatch):
        import os
        from pathlib import Path

        monkeypatch.setenv("SUPEX_AI_CONFIG_DIR", "~/cfg/supex-chat")
        assert config_dir() == Path(os.path.expanduser("~/cfg/supex-chat"))

    def test_linux_uses_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SUPEX_AI_CONFIG_DIR", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config_dir() == tmp_path / "supex-chat"

    def test_linux_falls_back_to_home_dot_config(self, monkeypatch):
        import os
        from pathlib import Path

        monkeypatch.delenv("SUPEX_AI_CONFIG_DIR", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config_dir() == Path(os.path.expanduser("~/.config")) / "supex-chat"

    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SUPEX_AI_CONFIG_DIR", raising=False)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
        assert config_dir() == tmp_path / "AppData" / "Roaming" / "supex-chat"

    def test_darwin_uses_application_support(self, monkeypatch):
        import os
        from pathlib import Path

        monkeypatch.delenv("SUPEX_AI_CONFIG_DIR", raising=False)
        monkeypatch.setattr("sys.platform", "darwin")
        assert (
            config_dir()
            == Path(os.path.expanduser("~/Library/Application Support")) / "supex-chat"
        )


@pytest.fixture
def profile_file(tmp_path):
    def _write(content):
        path = tmp_path / "profiles.toml"
        path.write_text(content, encoding="utf-8")
        return path

    return _write


class TestLoadProfiles:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_profiles(tmp_path / "nope.toml") == {}

    def test_parses_profiles_table(self, profile_file):
        path = profile_file(UNSLOTH_TOML)
        profiles = load_profiles(path)
        assert set(profiles) == {"openai", "unsloth"}
        assert profiles["unsloth"] == {
            "base_url": "http://localhost:8000",
            "api_key": "sk-unsloth-test",
            "model": "qwen3-local",
            "dialect": "anthropic",
        }

    def test_malformed_toml_raises(self, profile_file):
        path = profile_file("not [valid toml")
        with pytest.raises(ConfigError, match="could not read provider profiles"):
            load_profiles(path)

    def test_non_string_value_raises(self, profile_file):
        path = profile_file("[profiles.x]\nbase_url = 42\n")
        with pytest.raises(ConfigError, match="must be a string"):
            load_profiles(path)


class TestDefaultProfile:
    def test_returns_top_level_default(self, profile_file):
        path = profile_file(UNSLOTH_TOML)
        assert default_profile(path) == "unsloth"

    def test_none_when_missing(self, tmp_path):
        assert default_profile(tmp_path / "missing.toml") is None

    def test_none_when_not_string(self, profile_file):
        path = profile_file('default = 42\n[profiles.a]\nbase_url = "http://x"\n')
        assert default_profile(path) is None


class TestResolveProfile:
    def test_named_lookup(self, profile_file):
        path = profile_file(UNSLOTH_TOML)
        profile = resolve_profile("unsloth", path=path)
        assert profile is not None
        assert profile["base_url"] == "http://localhost:8000"

    def test_default_used_when_name_none(self, profile_file):
        path = profile_file(UNSLOTH_TOML)
        assert resolve_profile(None, path=path)["model"] == "qwen3-local"

    def test_none_when_no_name_and_no_default(self, tmp_path):
        path = tmp_path / "profiles.toml"
        path.write_text('[profiles.a]\nbase_url = "http://x"\n', encoding="utf-8")
        assert resolve_profile(None, path=path) is None

    def test_missing_file_none(self, tmp_path):
        assert resolve_profile(None, path=tmp_path / "nope.toml") is None

    def test_unknown_name_raises_with_available(self, profile_file):
        path = profile_file(UNSLOTH_TOML)
        with pytest.raises(ConfigError, match="unknown provider profile 'nope'"):
            resolve_profile("nope", path=path)

    def test_profiles_path_under_config_dir(self):
        assert profiles_path().name == "profiles.toml"
        assert profiles_path().parent == config_dir()

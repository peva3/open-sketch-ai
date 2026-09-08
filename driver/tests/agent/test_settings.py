"""Tests for the per-OS git-ignored settings.json loader/saver."""

from __future__ import annotations

import json
from pathlib import Path

from supex_driver.agent import settings


def test_settings_path_under_config_dir(tmp_path: Path) -> None:
    assert settings.settings_path(tmp_path) == tmp_path / "settings.json"


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert settings.load_settings(tmp_path / "settings.json") == {}


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = settings.settings_path(tmp_path)
    saved = settings.save_settings(
        {"base_url": "http://localhost:8000", "model": "qwen3-local"}, path
    )
    assert saved == {"base_url": "http://localhost:8000", "model": "qwen3-local"}
    assert settings.load_settings(path) == {
        "base_url": "http://localhost:8000",
        "model": "qwen3-local",
    }


def test_save_writes_pretty_json(tmp_path: Path) -> None:
    settings.save_settings({"model": "gpt-5"}, tmp_path)
    path = tmp_path / "settings.json"
    raw = path.read_text(encoding="utf-8")
    decoded = json.loads(raw)
    assert decoded["model"] == "gpt-5"
    assert raw.endswith("\n")


def test_load_ignores_unknown_keys_and_wrong_types(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "model": "gpt-5",
                "vision": True,
                "bogus_key": "oops",
                "max_tokens": "not-an-int",
                "retries": 5,
            }
        ),
        encoding="utf-8",
    )
    loaded = settings.load_settings(tmp_path)
    assert loaded["model"] == "gpt-5"
    assert loaded["vision"] is True
    assert "bogus_key" not in loaded
    assert "max_tokens" not in loaded
    assert loaded["retries"] == 5


def test_load_handles_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.load_settings(tmp_path) == {}


def test_load_handles_non_dict_json(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text('"just a string"', encoding="utf-8")
    assert settings.load_settings(tmp_path) == {}


def test_save_drops_non_scalar_unknown(tmp_path: Path) -> None:
    saved = settings.save_settings({"model": "x", "evil": {"nested": True}}, tmp_path)
    assert saved == {"model": "x"}


def test_settings_config_kwargs_projects_known_keys() -> None:
    kw = settings.settings_config_kwargs(
        {
            "base_url": "http://localhost:8000",
            "api_key": "sk-unsloth",
            "model": "qwen3",
            "dialect": "anthropic",
            "vision": True,
            "temperature": 0.3,
            "max_tokens": 1024,
            "max_iterations": 5,
            "timeout": 30.0,
            "retries": 1,
            "allow_delete": True,
        }
    )
    assert kw["base_url"] == "http://localhost:8000"
    assert kw["model"] == "qwen3"
    assert kw["temperature"] == 0.3
    assert kw["max_iterations"] == 5
    assert "allow_delete" not in kw
    assert "vision" in kw


def test_save_never_exposes_api_key_in_exceptions(tmp_path: Path) -> None:
    settings.save_settings({"api_key": "sk-secret"}, tmp_path)
    text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "sk-secret" in text


def test_sanitize_accepts_known_bool_and_numbers() -> None:
    clean = settings._sanitize(
        {
            "vision": True,
            "allow_delete": False,
            "retries": 2,
            "model": None,
            "dialect": "auto",
        }
    )
    assert clean["vision"] is True
    assert clean["allow_delete"] is False
    assert clean["retries"] == 2
    assert "model" not in clean

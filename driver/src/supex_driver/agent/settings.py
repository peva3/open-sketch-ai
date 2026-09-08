"""Persistent user settings for the supex-chat windowed app.

The chat UI lets the user edit provider/model/behaviour settings in the
browser. This module persists those as a small JSON file in the per-OS user
config directory (same location as :mod:`supex_driver.agent.profiles`
``profiles.toml``, but git-ignored and never bundled). It is a convenience
layer, not another precedence tier: explicit CLI flags and ``SUPEX_AI_*``
environment variables always win over anything stored here.

Only a fixed set of known keys is accepted; anything else is dropped on read
so a stale or hand-edited file cannot inject arbitrary config.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from supex_driver.agent.profiles import config_dir

logger = logging.getLogger("supex.agent.settings")

#: Keys the settings file may hold, with the type each value must satisfy.
_SETTINGS_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "base_url": str,
    "api_key": str,
    "model": str,
    "dialect": str,
    "vision": bool,
    "temperature": float,
    "max_tokens": int,
    "max_iterations": int,
    "timeout": float,
    "retries": int,
    "allow_delete": bool,
}

#: Names accepted verbatim by :func:`supex_driver.agent.config.load_config`.
_CONFIG_KEYS = (
    "base_url",
    "api_key",
    "model",
    "dialect",
    "timeout",
    "retries",
    "temperature",
    "max_tokens",
    "max_iterations",
    "vision",
)


def settings_path(path: Path | None = None) -> Path:
    """Return the settings file location under the per-OS config dir."""
    return (config_dir() if path is None else Path(path)) / "settings.json"


def _sanitize(data: Any) -> dict[str, Any]:
    """Keep only known keys with acceptable types; drop the rest."""
    if not isinstance(data, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, expected in _SETTINGS_SCHEMA.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, expected):
            clean[key] = value
    return clean


def load_settings(path: Path | None = None) -> dict[str, Any]:
    """Read and sanitize the settings file; ``{}`` on any failure.

    Never raises: a missing/corrupt/partly-invalid file simply yields the
    fields that are usable (dropped keys fall back to config/env defaults).
    """
    target = settings_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("ignoring unreadable settings file %s: %s", target, exc)
        return {}
    return _sanitize(raw)


def save_settings(data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Sanitize, merge nothing, and write ``data`` to the settings file.

    Returns the sanitized dict that was persisted. Creates the parent
    directory if needed. Does not log the api_key.
    """
    target = settings_path(path)
    clean = _sanitize(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return clean


def settings_config_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """Project a settings dict onto the kwargs :func:`load_config` accepts.

    Unknown keys (e.g. ``allow_delete``) are excluded so they do not cause a
    ``TypeError`` when passed to :func:`supex_driver.agent.config.load_config`.
    """
    return {key: data[key] for key in _CONFIG_KEYS if key in data}

"""Named provider profiles stored on disk (git-ignored user data).

Profiles let a user keep one ``profiles.toml`` file with their favourite
endpoints (OpenAI, Azure, OpenRouter, Groq, Ollama, LM Studio, vLLM,
Unsloth, ...) and switch with ``supex-chat --provider-profile unsloth``
instead of repeating ``--base-url/--api-key/--model`` on every run.

Precedence is enforced in :func:`supex_driver.agent.config.load_config`:
CLI flags > ``SUPEX_AI_*`` env > ``OPENAI_*``/``ANTHROPIC_*`` env >
profile fields > built-in defaults. A profile field therefore only fills
gaps the user did not already cover with flags or environment variables.

File format (TOML, parsed with the stdlib ``tomllib``):

.. code-block:: toml

   default = "unsloth"                # optional: used when no --provider-profile

   [profiles.openai]
   base_url = "https://api.openai.com/v1"
   model = "gpt-5"
   dialect = "openai"                 # optional; auto-sniffed when absent

   [profiles.unsloth]
   base_url = "http://localhost:8000"
   api_key = "sk-unsloth-…"           # secrets live here, never in git
   model = "qwen3-…"
   dialect = "anthropic"

The file lives in the per-OS user config directory (see
:func:`config_dir`), overridable with ``SUPEX_AI_CONFIG_DIR``.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from supex_driver.agent.errors import ConfigError

_CONFIG_FILENAME = "profiles.toml"
_APP_DIR = "supex-chat"

_ProfileFile = dict[str, dict[str, str]]


def config_dir() -> Path:
    """Return the per-OS user configuration directory for supex-chat.

    Resolution order:

    * ``SUPEX_AI_CONFIG_DIR`` (explicit override)
    * Windows: ``%APPDATA%\\supex-chat``
    * macOS: ``~/Library/Application Support/supex-chat``
    * Linux/other: ``$XDG_CONFIG_HOME/supex-chat`` or ``~/.config/supex-chat``
    """
    override = os.environ.get("SUPEX_AI_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / _APP_DIR


def profiles_path() -> Path:
    """Return the profiles file path (does not require it to exist)."""
    return config_dir() / _CONFIG_FILENAME


def _read(path: Path) -> tuple[dict[str, dict[str, str]], str | None]:
    """Parse the profiles file; returns ``(profiles, default_name)``.

    A missing file yields ``({}, None)``. Malformed TOML, wrong section
    shape, or non-string values raise :class:`ConfigError`.
    """
    if not path.is_file():
        return {}, None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read provider profiles {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"provider profiles {path}: expected a TOML table")

    default = data.get("default")
    default_name = default if isinstance(default, str) and default else None

    sections = data.get("profiles", {})
    if not isinstance(sections, dict):
        raise ConfigError(f"provider profiles {path}: 'profiles' must be a table")
    profiles: dict[str, dict[str, str]] = {}
    for name, table in sections.items():
        if not isinstance(table, dict):
            raise ConfigError(
                f"provider profiles {path}: profile {name!r} must be a table"
            )
        fields: dict[str, str] = {}
        for field, value in table.items():
            if not isinstance(value, str):
                raise ConfigError(
                    f"provider profiles {path}: {name}.{field} must be a string"
                )
            fields[field] = value
        profiles[str(name)] = fields
    return profiles, default_name


def load_profiles(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Parse the profiles file into ``{profile_name: {field: value}}``."""
    profiles, _ = _read(path if path is not None else profiles_path())
    return profiles


def default_profile(path: Path | None = None) -> str | None:
    """Return the top-level ``default = "…"`` profile name, if declared."""
    _, default = _read(path if path is not None else profiles_path())
    return default


def resolve_profile(
    name: str | None,
    *,
    path: Path | None = None,
) -> dict[str, str] | None:
    """Look up a named profile, honoring ``default = "…"`` when name is None.

    Returns ``None`` when no profile is requested and no default is
    declared (the caller then relies on flags/env only). An unknown
    requested name raises :class:`ConfigError` listing available profiles.
    """
    p = path if path is not None else profiles_path()
    profiles, default = _read(p)
    requested = name or default
    if requested is None:
        return None
    if requested in profiles:
        return profiles[requested]
    available = ", ".join(sorted(profiles)) if profiles else "(none configured)"
    raise ConfigError(
        f"unknown provider profile {requested!r}; available profiles: {available}"
    )

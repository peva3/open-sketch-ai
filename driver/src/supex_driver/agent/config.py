"""Provider configuration resolution for supex-chat.

Resolution precedence is: explicit keyword arguments (CLI flags) >
``SUPEX_AI_*`` environment variables > provider-standard environment
variables (``OPENAI_*`` / ``ANTHROPIC_*``) > built-in defaults.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from supex_driver.agent.errors import ConfigError

Dialect = Literal["auto", "openai", "anthropic"]
AuthStyle = Literal["auto", "x-api-key", "bearer"]

_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
_ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"


def _bool_env(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    value = env.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def detect_dialect(
    base_url: str | None,
    api_key: str | None,
    forced: Dialect,
) -> Literal["openai", "anthropic"]:
    """Resolve an effective dialect from an explicit choice, URL, or key shape."""
    if forced == "openai":
        return "openai"
    if forced == "anthropic":
        return "anthropic"
    if base_url:
        lower = base_url.lower()
        if "anthropic" in lower or lower.rstrip("/").endswith("/v1/messages"):
            return "anthropic"
        return "openai"
    if api_key and api_key.startswith("sk-ant-"):
        return "anthropic"
    return "openai"


def _env_group(effective: str) -> tuple[str, str, str]:
    if effective == "anthropic":
        return "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"
    return "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"


@dataclass(frozen=True)
class ProviderConfig:
    """Resolved settings for talking to one model provider endpoint."""

    base_url: str
    api_key: str | None = field(default=None, repr=False)
    model: str | None = None
    dialect: Dialect = "auto"
    timeout: float = 60.0
    temperature: float | None = None
    max_tokens: int | None = None
    max_iterations: int = 10
    vision: bool = False
    auth_header: AuthStyle = "auto"
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def effective_dialect(self) -> Literal["openai", "anthropic"]:
        """Resolved dialect (never ``auto``)."""
        return detect_dialect(self.base_url, self.api_key, self.dialect)

    @property
    def is_local(self) -> bool:
        """True when the endpoint is loopback (localhost/127.0.0.1/::1)."""
        return "localhost" in self.base_url.lower() or "127.0.0.1" in self.base_url


def load_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    dialect: Dialect = "auto",
    timeout: float | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_iterations: int | None = None,
    vision: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> ProviderConfig:
    """Build a :class:`ProviderConfig` from explicit args + env fallbacks.

    Args are honored first, then ``SUPEX_AI_*`` variables, then the
    provider-standard ``OPENAI_*``/``ANTHROPIC_*`` group matching the
    effective dialect.
    """
    env = os.environ if env is None else env

    supex_base = env.get("SUPEX_AI_BASE_URL")
    supex_key = env.get("SUPEX_AI_API_KEY")
    supex_model = env.get("SUPEX_AI_MODEL")
    candidate_key = api_key or supex_key
    candidate_base = base_url or supex_base

    if dialect in ("openai", "anthropic"):
        effective: Literal["openai", "anthropic"] = dialect
    elif candidate_base:
        effective = detect_dialect(candidate_base, candidate_key, "auto")
    elif env.get("ANTHROPIC_BASE_URL") and not env.get("OPENAI_BASE_URL"):
        effective = "anthropic"
    else:
        effective = detect_dialect(None, candidate_key, "auto")

    std_base, std_key, std_model = _env_group(effective)

    resolved_base = candidate_base or env.get(std_base)
    if resolved_base is None:
        resolved_base = (
            _ANTHROPIC_DEFAULT_BASE
            if effective == "anthropic"
            else _OPENAI_DEFAULT_BASE
        )

    resolved_key = candidate_key or env.get(std_key)
    if resolved_key is None and effective == "anthropic":
        resolved_key = env.get("ANTHROPIC_AUTH_TOKEN")

    resolved_model = model or supex_model or env.get(std_model)
    if not resolved_model:
        raise ConfigError(
            "no model configured; set SUPEX_AI_MODEL (or OPENAI_MODEL / "
            "ANTHROPIC_MODEL) or pass --model"
        )

    temperature_value = (
        temperature
        if temperature is not None
        else _float_env(env, "SUPEX_AI_TEMPERATURE", float("nan"))
    )
    max_tokens_value = (
        max_tokens
        if max_tokens is not None
        else _int_env(env, "SUPEX_AI_MAX_TOKENS", -1)
    )
    auth_style = env.get("SUPEX_AI_AUTH_STYLE")
    resolved_auth: AuthStyle = (
        cast(AuthStyle, auth_style)
        if auth_style in ("auto", "x-api-key", "bearer")
        else "auto"
    )

    return ProviderConfig(
        base_url=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        dialect=effective,
        timeout=timeout
        if timeout is not None
        else _float_env(env, "SUPEX_AI_TIMEOUT", 60.0),
        temperature=None if math.isnan(temperature_value) else temperature_value,
        max_tokens=None if max_tokens_value <= 0 else max_tokens_value,
        max_iterations=max_iterations
        if max_iterations is not None
        else _int_env(env, "SUPEX_AI_MAX_ITERATIONS", 10),
        vision=vision
        if vision is not None
        else _bool_env(env, "SUPEX_AI_VISION", False),
        auth_header=resolved_auth,
    )

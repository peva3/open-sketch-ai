"""supex-chat: provider-agnostic AI agent for SketchUp.

The agent runs in the driver process and drives a remote model endpoint
(any OpenAI- or Anthropic-compatible API) in an agentic loop. The loop's
SketchUp backend is the existing Supex MCP server (spawned over stdio),
so the SketchUp Ruby runtime is never modified.
"""

from supex_driver.agent.config import ProviderConfig, load_config
from supex_driver.agent.errors import (
    AgentError,
    BackendConnectionError,
    BackendError,
    BackendToolError,
    ConfigError,
    FileToolError,
    GuideError,
    PathNotAllowedError,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderHTTPError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

__all__ = [
    "AgentError",
    "BackendConnectionError",
    "BackendError",
    "BackendToolError",
    "ConfigError",
    "FileToolError",
    "GuideError",
    "PathNotAllowedError",
    "ProviderAuthError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderHTTPError",
    "ProviderProtocolError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderConfig",
    "load_config",
]

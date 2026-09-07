"""Shared exception types for the supex-chat agent and its providers."""


class AgentError(Exception):
    """Base class for all supex-chat agent errors."""


class ConfigError(AgentError):
    """Raised when agent/provider configuration is invalid or missing."""


class GuideError(AgentError):
    """Raised when the SketchUp agent-guide content cannot be located or read."""


class ProviderError(AgentError):
    """Base class for model-provider failures."""


class ProviderAuthError(ProviderError):
    """Raised when the provider rejects the supplied API key/credentials."""


class ProviderConnectionError(ProviderError):
    """Raised when the provider endpoint cannot be reached."""


class ProviderTimeoutError(ProviderConnectionError):
    """Raised when the provider does not respond within the timeout."""


class ProviderRateLimitError(ProviderError):
    """Raised when the provider returns HTTP 429."""


class ProviderHTTPError(ProviderError):
    """Raised when the provider returns a non-success HTTP status."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"provider returned HTTP {status_code}: {message}")


class ProviderProtocolError(ProviderError):
    """Raised when a provider response cannot be parsed or is malformed."""


class BackendError(AgentError):
    """Base class for SketchUp-backend (Supex MCP server) failures."""


class BackendConnectionError(BackendError):
    """Raised when the MCP backend cannot be spawned, reached, or kept alive."""


class BackendToolError(BackendError):
    """Raised when a backend tool call fails remotely (is_error result)."""

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(f"tool {name!r} failed: {message}")


class FileToolError(AgentError):
    """Base class for agent workspace file-tool failures."""


class PathNotAllowedError(FileToolError):
    """Raised when a file path escapes the workspace / allowed roots."""

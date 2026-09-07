"""Provider factory: pick an OpenAI or Anthropic dialect from config."""

from __future__ import annotations

from supex_driver.agent.config import ProviderConfig
from supex_driver.agent.errors import ProviderConnectionError
from supex_driver.agent.providers.anthropic import AnthropicProvider
from supex_driver.agent.providers.base import ChatProvider
from supex_driver.agent.providers.openai import OpenAIProvider

__all__ = ["ChatProvider", "build_provider", "list_models"]


def build_provider(config: ProviderConfig) -> ChatProvider:
    """Construct the provider matching ``config.effective_dialect``."""
    if config.effective_dialect == "anthropic":
        return AnthropicProvider(config)
    return OpenAIProvider(config)


async def list_models(config: ProviderConfig) -> list[str]:
    """Return model identifiers advertised by the configured endpoint.

    Raises :class:`ProviderError` subclasses on connection failure;
    endpoints that do not implement a model listing simply return an
    empty list.
    """
    provider = build_provider(config)
    try:
        return await provider.list_models()
    except ProviderConnectionError:
        raise
    finally:
        await provider.aclose()

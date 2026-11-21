"""Chat providers for Ask Foodo. ``get_provider`` returns ``None`` when not configured."""

from __future__ import annotations

import logging

from .base import ChatProvider, ProviderError, ProviderNotConfiguredError, Reply

logger = logging.getLogger(__name__)

__all__ = ["ChatProvider", "ProviderError", "ProviderNotConfiguredError", "Reply", "get_provider"]


def get_provider(name: str, model: str = "") -> ChatProvider | None:
    """The provider ``name`` using ``model``; ``None`` for a missing SDK/key or unknown name."""
    try:
        if name == "anthropic":
            from .anthropic import AnthropicProvider

            return AnthropicProvider(model)
        if name == "openai":
            from .openai import OpenAIProvider

            return OpenAIProvider(model)
        from .catalog import PROVIDERS

        if name in PROVIDERS:  # Gemini, Groq, OpenRouter, Qwen, Ollama: OpenAI-compatible APIs
            from .openai import CompatibleProvider

            return CompatibleProvider(name, model)
        if name == "fake":
            from .fake import FakeProvider

            return FakeProvider(model)
    except ProviderNotConfiguredError as exc:
        logger.info("AI provider %s not configured: %s", name, exc)
        return None
    logger.warning("Unknown AI provider %r", name)
    return None

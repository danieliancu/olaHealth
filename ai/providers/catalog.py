"""The AI providers Ask Foodo can use, and the models each one offers.

Every provider has its own key in the environment (never in the database). The model list is
read live from the provider when its key is set (cached for an hour) and falls back to a short
built-in list, so the staff Settings page can always offer the models of the chosen provider.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from django.core.cache import cache

logger = logging.getLogger(__name__)

LIST_TIMEOUT_SECONDS = 5.0
CACHE_SECONDS = 3600


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    env_key: str = ""  # environment variable holding the API key ("" = none needed)
    base_url: str = ""  # OpenAI-compatible endpoint ("" = the vendor's own SDK)
    base_url_env: str = ""  # optional override of base_url
    default_model: str = ""
    fallback_models: tuple[str, ...] = ()
    include: str = ""  # regex: model ids worth offering for chat
    exclude: str = (
        r"embed|whisper|tts|transcri|audio|realtime|image|dall-e|moderation|guard|search"
        r"|vision-preview|aqa|imagen|veo"
    )
    extra: dict = field(default_factory=dict)

    def api_key(self) -> str:
        return os.environ.get(self.env_key, "").strip() if self.env_key else ""

    def url(self) -> str:
        return (
            os.environ.get(self.base_url_env, "").strip() if self.base_url_env else ""
        ) or self.base_url

    @property
    def configured(self) -> bool:
        return bool(self.api_key()) if self.env_key else bool(self.url())


PROVIDERS: dict[str, ProviderInfo] = {
    p.key: p
    for p in (
        ProviderInfo(
            "anthropic", "Anthropic (Claude)", env_key="ANTHROPIC_API_KEY",
            default_model="claude-haiku-5-5",
            fallback_models=(
                "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2",
            ),
            include=r"^claude-",
        ),
        ProviderInfo(
            "openai", "OpenAI", env_key="OPENAI_API_KEY",
            default_model="gpt-5-nano",
            fallback_models=("gpt-5-nano", "gpt-5-mini", "gpt-5"),
            include=r"^(gpt-|o\d|chatgpt-)",
        ),
        ProviderInfo(
            "gemini", "Google Gemini", env_key="GEMINI_API_KEY",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            default_model="gemini-2.5-flash-lite",
            fallback_models=("gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"),
            include=r"^gemini-",
        ),
        ProviderInfo(
            "groq", "Groq", env_key="GROQ_API_KEY",
            base_url="https://api.groq.com/openai/v1",
            default_model="llama-3.1-8b-instant",
            fallback_models=("llama-3.1-8b-instant", "llama-3.3-70b-versatile", "qwen/qwen3-32b"),
        ),
        ProviderInfo(
            "openrouter", "OpenRouter", env_key="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai/api/v1",
            default_model="openrouter/auto",
            fallback_models=("openrouter/auto",),
        ),
        ProviderInfo(
            "qwen", "Alibaba Qwen (DashScope)", env_key="DASHSCOPE_API_KEY",
            base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            base_url_env="DASHSCOPE_BASE_URL",
            default_model="qwen-turbo",
            fallback_models=("qwen-turbo", "qwen-plus", "qwen-max"),
            include=r"^qwen",
        ),
        ProviderInfo(
            "ollama", "Ollama (local, e.g. Qwen)",
            base_url_env="OLLAMA_BASE_URL",
            default_model="qwen3:8b",
            fallback_models=("qwen3:8b", "llama3.2"),
        ),
    )
}  # fmt: skip

CHOICES = [(p.key, p.label) for p in PROVIDERS.values()]


def _live_models(info: ProviderInfo) -> list[str]:
    if info.key == "anthropic":
        import anthropic

        client = anthropic.Anthropic(
            api_key=info.api_key(), timeout=LIST_TIMEOUT_SECONDS, max_retries=0
        )
        return [m.id for m in client.models.list(limit=100)]
    import openai

    client = openai.OpenAI(
        api_key=info.api_key() or "not-needed",
        base_url=info.url() or None,
        timeout=LIST_TIMEOUT_SECONDS,
        max_retries=0,
    )
    return [m.id.removeprefix("models/") for m in client.models.list()]


def _keep(info: ProviderInfo, model: str) -> bool:
    if info.include and not re.search(info.include, model):
        return False
    return not (info.exclude and re.search(info.exclude, model, re.IGNORECASE))


def models_for(key: str) -> tuple[list[str], bool]:
    """The chat models of provider ``key`` and whether the list came live from the provider."""
    info = PROVIDERS.get(key)
    if info is None:
        return [], False
    cache_key = f"ai-models:{key}"
    found = cache.get(cache_key)
    if found is None and info.configured:
        try:
            found = sorted({m for m in _live_models(info) if _keep(info, m)})
        except Exception as exc:
            logger.info("Listing %s models failed: %s", key, type(exc).__name__)
            found = []
        cache.set(cache_key, found, CACHE_SECONDS if found else 300)
    if found:
        return found, True
    return list(info.fallback_models), False

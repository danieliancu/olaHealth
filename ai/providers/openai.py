"""OpenAI adapter on Chat Completions (the long-stable endpoint). ``openai`` is imported lazily."""

from __future__ import annotations

import os
import re

from .base import ProviderError, ProviderNotConfiguredError, Reply

DEFAULT_MODEL = "gpt-5-nano"
# Models known to accept reasoning_effort="minimal" (the retry after an empty answer).
MINIMAL_EFFORT = re.compile(r"^gpt-5(-mini|-nano)?(-\d{4}-\d{2}-\d{2})?$")
TIMEOUT_SECONDS = 10.0  # well inside the web worker timeout; no retry


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "", api_key: str | None = None, base_url: str | None = None):
        self.model = model or DEFAULT_MODEL
        api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ProviderNotConfiguredError("OPENAI_API_KEY is not set")
        self._base_url = base_url
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotConfiguredError("the openai package is not installed") from exc
        self._sdk = openai
        self._client = openai.OpenAI(
            api_key=api_key, base_url=base_url or None, timeout=TIMEOUT_SECONDS, max_retries=0
        )

    def reply(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        *,
        short_thinking: bool = False,
    ) -> Reply:
        extra = {}
        if not self._base_url and self.model.startswith(("gpt-5", "o")):
            # Reasoning models spend output tokens on reasoning first; keep that short
            # (and as short as the model allows on the retry after an empty answer).
            minimal = short_thinking and MINIMAL_EFFORT.match(self.model)
            extra["reasoning_effort"] = "minimal" if minimal else "low"
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
                **extra,
            )
        except self._sdk.OpenAIError as exc:
            raise ProviderError(f"openai: {type(exc).__name__}") from exc
        usage = response.usage
        spent = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "model": str(getattr(response, "model", "") or self.model),
        }
        message = response.choices[0].message if response.choices else None
        if message is not None and getattr(message, "refusal", None):
            raise ProviderError("openai: refusal", **spent)
        text = ((message.content if message is not None else "") or "").strip()
        if not text:
            raise ProviderError("openai: empty answer", empty=True, **spent)
        return Reply(text=text, **spent)


class CompatibleProvider(OpenAIProvider):
    """A provider speaking the OpenAI Chat Completions API at its own address (Google Gemini,
    Groq, OpenRouter, Alibaba Qwen, a local Ollama), each with its own key; see ``catalog``."""

    def __init__(self, provider: str, model: str = ""):
        from .catalog import PROVIDERS

        info = PROVIDERS[provider]
        self.name = info.key
        base_url = info.url()
        if not base_url:
            raise ProviderNotConfiguredError(f"{info.base_url_env} is not set")
        api_key = info.api_key()
        if info.env_key and not api_key:
            raise ProviderNotConfiguredError(f"{info.env_key} is not set")
        # Local servers (Ollama) ignore the key; the SDK still wants a non-empty one.
        super().__init__(
            model or info.default_model, api_key=api_key or "not-needed", base_url=base_url
        )

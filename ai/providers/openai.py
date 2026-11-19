"""OpenAI adapter on Chat Completions (the long-stable endpoint). ``openai`` is imported lazily."""

from __future__ import annotations

import os

from .base import ProviderError, ProviderNotConfiguredError, Reply

DEFAULT_MODEL = "gpt-5-nano"
TIMEOUT_SECONDS = 20.0


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str = "", api_key: str | None = None):
        self.model = model or DEFAULT_MODEL
        api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ProviderNotConfiguredError("OPENAI_API_KEY is not set")
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotConfiguredError("the openai package is not installed") from exc
        self._sdk = openai
        self._client = openai.OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)

    def reply(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> Reply:
        extra = {}
        if self.model.startswith(("gpt-5", "o")):
            # Reasoning models spend output tokens on reasoning first; keep that short.
            extra["reasoning_effort"] = "low"
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                max_completion_tokens=max_tokens,
                **extra,
            )
        except self._sdk.OpenAIError as exc:
            raise ProviderError(f"openai: {type(exc).__name__}") from exc
        message = response.choices[0].message if response.choices else None
        if message is not None and getattr(message, "refusal", None):
            raise ProviderError("openai: refusal")
        text = ((message.content if message is not None else "") or "").strip()
        if not text:
            raise ProviderError("openai: empty answer")
        usage = response.usage
        return Reply(
            text=text,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            model=str(getattr(response, "model", "") or self.model),
        )

"""Anthropic (Claude) adapter. The ``anthropic`` SDK is imported lazily."""

from __future__ import annotations

import os

from .base import ProviderError, ProviderNotConfiguredError, Reply

DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_SECONDS = 20.0


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = "", api_key: str | None = None):
        self.model = model or DEFAULT_MODEL
        api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderNotConfiguredError("the anthropic package is not installed") from exc
        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)

    def reply(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> Reply:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                # Thinking is adaptive by default on this model; keep it cheap and fast.
                output_config={"effort": "low"},
            )
        except self._sdk.APIError as exc:
            raise ProviderError(f"anthropic: {type(exc).__name__}") from exc
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError("anthropic: refusal")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            raise ProviderError("anthropic: empty answer")
        usage = response.usage
        return Reply(
            text=text,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            model=str(getattr(response, "model", "") or self.model),
        )

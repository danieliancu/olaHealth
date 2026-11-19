"""Provider-neutral types for Ask Foodo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """Any provider failure (network, API error, refusal, empty answer)."""


class ProviderNotConfiguredError(ProviderError):
    """The SDK is not installed or the API key is missing."""


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ChatProvider(Protocol):
    name: str
    model: str

    def reply(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> Reply: ...

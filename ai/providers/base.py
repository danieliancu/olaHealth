"""Provider-neutral types for Ask Foodo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """Any provider failure (network, API error, refusal, empty answer).

    A refusal or an empty answer still used tokens: they are carried here so the usage is
    counted against the limits."""

    def __init__(
        self,
        message: str = "",
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
        empty: bool = False,
    ):
        super().__init__(message)
        # True when the model answered with no text (usually: its thinking used the whole
        # allowance). Such a call is worth one retry; a refusal or an API error is not.
        self.empty = empty
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model


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

    def reply(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        *,
        short_thinking: bool = False,
    ) -> Reply: ...

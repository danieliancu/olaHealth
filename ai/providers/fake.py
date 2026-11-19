"""Deterministic provider for tests and local development (provider name ``fake``)."""

from __future__ import annotations

import json

from .base import ProviderError, Reply


class FakeProvider:
    name = "fake"

    def __init__(self, model: str = "", *, fail: bool = False):
        self.model = model or "fake-1"
        self.fail = fail
        self.calls: list[dict] = []

    def reply(self, system: str, messages: list[dict[str, str]], max_tokens: int) -> Reply:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        if self.fail:
            raise ProviderError("fake: failure")
        last = messages[-1]["content"] if messages else ""
        facts = last.split("FACTS:", 1)[-1].split("QUESTION:", 1)[0].strip()
        try:
            facts = json.dumps(json.loads(facts), sort_keys=True, ensure_ascii=False)
        except ValueError:
            facts = ""
        text = f"Based on the facts: {facts}"
        return Reply(
            text=text,
            input_tokens=len(system) + len(last),
            output_tokens=len(text),
            model=self.model,
        )

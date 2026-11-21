"""The interface every personal-context source implements (see ``myola.context``)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol


class NotConfigured(Exception):
    """The source cannot be used here (for example its database is not connected yet)."""


class ContextSource(Protocol):
    name: str

    def user_context(self, user, question: str, *, today: dt.date) -> dict[str, Any]:
        """What is known about ``user`` that helps answer ``question``, as plain JSON-ready
        data. Only sections relevant to the question; never name, email or health data."""

    def available_products(self, user, *, query: str = "", limit: int = 6) -> list[dict[str, Any]]:
        """Products sold where the user lives, matching ``query`` (or close to what they buy),
        best first. Never their Avoid list or products with unusable data."""

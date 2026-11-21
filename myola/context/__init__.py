"""Personal context for Ask Foodo: what the AI may read about a user to answer questions about
them (their profile, diary, water, activity, weight, lists, scans, memories, notes) and which
products are available where they live. A retrieval layer: only the parts relevant to the
question are sent, and only with the user's consent (``Profile.ai_use_my_data``).

The source is chosen by ``settings.AI_CONTEXT_SOURCE``:

* "django" (default): the app's own models (``django_source.DjangoContextSource``);
* "local_db": the separate local database with the geographic product catalogue, once it
  exists. Until then it raises ``NotConfigured`` and the Django source is used, so switching
  later is only a new class here and an environment variable.
"""

from __future__ import annotations

import logging

from django.conf import settings

from myola.context.base import ContextSource, NotConfigured

logger = logging.getLogger(__name__)


class LocalDatabaseSource:
    """Placeholder for the local database (personal data + products by location)."""

    name = "local_db"

    def __init__(self) -> None:
        url = getattr(settings, "AI_CONTEXT_DB_URL", "")
        if not url:
            raise NotConfigured("AI_CONTEXT_DB_URL is not set")
        raise NotConfigured("the local context database is not connected yet")


def _django():
    from myola.context.django_source import DjangoContextSource

    return DjangoContextSource()


SOURCES = {"django": _django, "local_db": LocalDatabaseSource}


def get_source(name: str = "") -> ContextSource:
    """The configured source; the Django one when the configured source cannot be used."""
    name = name or getattr(settings, "AI_CONTEXT_SOURCE", "django") or "django"
    factory = SOURCES.get(name)
    if factory is not None and name != "django":
        try:
            return factory()
        except NotConfigured as exc:
            logger.warning("Context source %s not available (%s): using django", name, exc)
    return _django()


__all__ = ["SOURCES", "ContextSource", "NotConfigured", "get_source"]

"""Text embeddings for Ask Foodo's Magazine search (OpenAI ``text-embedding-3-small``).

Used only for published Magazine passages and the user's question, never for user data.
``embed()`` returns ``None`` whenever embeddings cannot be used now; the caller then falls back
to PostgreSQL full-text search. That is the case when:

* AI is disabled in the staff panel (``SiteSettings.ai_enabled``);
* this month's AI cost (all users) reached ``ai_monthly_budget_gbp``;
* ``OPENAI_API_KEY`` is not set or the ``openai`` package is missing;
* the call fails (network, API error).

The cost of a question's embedding is added to that user's ``AIUsage`` (tokens only: it is not a
message); indexing articles is a few thousand tokens per rebuild and is not attributed to anyone.
Vectors are normalised to length 1, so the cosine similarity is a dot product.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

MODEL = "text-embedding-3-small"
TIMEOUT_SECONDS = 8.0
BATCH = 64
QUERY_CACHE_SECONDS = 24 * 3600


def _client():
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None
    try:
        import openai
    except ImportError:
        return None
    return openai.OpenAI(api_key=key, timeout=TIMEOUT_SECONDS, max_retries=0)


def allowed() -> bool:
    """AI on and under this month's budget (the same switches as the chat answers)."""
    from ai.service import _month_cost
    from staff.models import SiteSettings

    site = SiteSettings.load()
    if not site.ai_enabled:
        return False
    return site.to_gbp(_month_cost(timezone.localdate())) < site.ai_monthly_budget_gbp


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product of two normalised vectors."""
    return sum(x * y for x, y in zip(a, b, strict=False))


def embed(texts: list[str], *, user=None) -> list[list[float]] | None:
    """One normalised vector per text, or None (see the module docstring)."""
    texts = [t.strip()[:8000] for t in texts]
    if not texts or not allowed():
        return None
    client = _client()
    if client is None:
        return None
    import openai

    out: list[list[float]] = []
    tokens = 0
    try:
        for start in range(0, len(texts), BATCH):
            response = client.embeddings.create(model=MODEL, input=texts[start : start + BATCH])
            out += [normalise(list(d.embedding)) for d in response.data]
            tokens += int(getattr(response.usage, "total_tokens", 0) or 0)
    except openai.OpenAIError as exc:
        logger.warning("Embeddings failed: %s", type(exc).__name__)
        return None
    if user is not None and tokens:
        from ai.models import AIUsage
        from ai.service import add_tokens

        AIUsage.objects.get_or_create(user=user, day=timezone.localdate())
        add_tokens(user, MODEL, tokens, 0)
    return out


def embed_query(text: str, *, user=None) -> list[float] | None:
    """The question's vector, cached for a day (the same question costs once)."""
    key = "embed:q:" + hashlib.sha256(f"{MODEL}\n{text.strip().lower()}".encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    vectors = embed([text], user=user)
    if not vectors:
        return None
    cache.set(key, vectors[0], QUERY_CACHE_SECONDS)
    return vectors[0]

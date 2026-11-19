"""What Ask Foodo may remember: manual memories and suggestions the user confirms.

Only memories that are active *and* confirmed are ever used in replies. Suggestions come only
from recorded preferences and actions (goal, diets, the Avoid list) and do nothing until the
user confirms them. Diagnoses, symptoms and medication are never stored as memories.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils import timezone

from myola import preferences as P
from myola.models import LIMITS, Memory, ProductMark
from myola.services.profiles import derive_prefs, get_profile

# Health words that must never become a memory (the UI never asks for them either).
_MEDICAL = re.compile(
    r"\b(diagnos\w*|symptom\w*|medica\w*|medicine\w*|prescri\w*|pills?|tablets?|insulin|"
    r"metformin|statins?|antidepress\w*|dos(e|es|age)|\d+\s?mg|mg|diabet\w*|hypertensi\w*|"
    r"blood pressure|depress\w*|anorexi\w*|bulimi\w*|eating disorder\w*|therap\w*)\b",
    re.IGNORECASE,
)


class MemoryRefused(ValueError):
    """The text looks like health information, which is never stored as a memory."""


def looks_medical(text: str) -> bool:
    return bool(_MEDICAL.search(text or ""))


def used_in_replies(user):
    """The memories Ask Foodo may use: active and confirmed."""
    return Memory.objects.filter(user=user, active=True, confirmed_at__isnull=False)


def suggestions(user, profile=None) -> list[tuple[str, str]]:
    """``(key, text)`` pairs from recorded preferences and actions, minus the ones already
    turned into memories (confirmed, paused or later edited)."""
    profile = profile or get_profile(user)
    out: list[tuple[str, str]] = []
    if profile.goal in P.GOALS:
        out.append((f"goal:{profile.goal}", f"My goal: {str(P.GOALS[profile.goal][0]).lower()}."))
    for diet in derive_prefs(profile)["diets"]:
        out.append((f"diet:{diet}", f"I choose {str(P.DIETS[diet][0]).lower()} products."))
    avoid = (
        ProductMark.objects.filter(user=user, kind=ProductMark.Kind.AVOID)
        .select_related("product")
        .order_by("-created_at")[:3]
    )
    for mark in avoid:
        name = mark.product.name or "Unnamed product"
        out.append((f"avoid:{mark.product_id}", f"I avoid “{name}”."))
    known = set(
        Memory.objects.filter(user=user)
        .exclude(suggestion_key="")
        .values_list("suggestion_key", flat=True)
    )
    return [(key, text) for key, text in out if key not in known]


def _check(text: str) -> str:
    text = (text or "").strip()[:300]
    if not text:
        raise MemoryRefused("empty")
    if looks_medical(text):
        raise MemoryRefused("medical")
    return text


@transaction.atomic
def add(user, text: str) -> Memory:
    """A memory typed by the user: confirmed by the act of adding it."""
    text = _check(text)
    memory = Memory.objects.create(
        user=user, text=text, source=Memory.Source.MANUAL, confirmed_at=timezone.now()
    )
    _trim(user)
    return memory


@transaction.atomic
def confirm_suggestion(user, key: str) -> Memory | None:
    """Turn a current suggestion into a confirmed memory; None if it is not on offer."""
    text = dict(suggestions(user)).get(key)
    if text is None:
        return None
    memory = Memory.objects.create(
        user=user,
        text=text,
        source=Memory.Source.SUGGESTION,
        suggestion_key=key,
        confirmed_at=timezone.now(),
    )
    _trim(user)
    return memory


def edit(memory: Memory, text: str) -> Memory:
    memory.text = _check(text)
    memory.confirmed_at = timezone.now()
    memory.save(update_fields=["text", "confirmed_at"])
    return memory


def toggle_active(memory: Memory) -> bool:
    memory.active = not memory.active
    memory.save(update_fields=["active"])
    return memory.active


def _trim(user) -> None:
    stale = list(
        Memory.objects.filter(user=user)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)[LIMITS["memories"] :]
    )
    if stale:
        Memory.objects.filter(pk__in=stale).delete()

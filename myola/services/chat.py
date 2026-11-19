"""Ask Foodo (MVP): deterministic answers built only from server data.

Three steps, so that an AI provider can later replace only the wording:

* ``detect_intent(text)`` picks one of the known intents (or None);
* ``build_facts(user, context, product)`` collects the facts the answer may use: the product's
  recorded values, Nutri-Score, processing level and restriction states, the user's preferences,
  lists and scans, confirmed and active memories, and notes only when the user allows it;
* ``rule_answer(intent, facts)`` phrases the answer from those facts alone.

It never invents values, ingredients, effects, availability or compatibility, and gives no
medical advice. Messages are kept per context (``personal`` or ``product:<uuid>``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from django.db import transaction
from django.utils.formats import date_format

from myola import preferences as P
from myola.models import LIMITS, ChatMessage, Note, ProductMark
from myola.services import lists
from myola.services.memories import used_in_replies
from myola.services.profiles import derive_prefs, get_profile

PERSONAL = "personal"

SUGGESTIONS: dict[str, list[tuple[str, str]]] = {
    "product": [
        ("values", "What are the key values?"),
        ("indicators", "What do Nutri-Score and processing mean?"),
        ("restrictions", "Does it fit my restrictions?"),
        ("remember", "What do you remember?"),
    ],
    "personal": [
        ("remember", "What do you remember?"),
        ("avoid", "What did I mark as Avoid?"),
        ("scans", "What did I scan recently?"),
        ("prefs", "What are my preferences?"),
    ],
}
INTENTS = ("values", "indicators", "restrictions", "remember", "avoid", "scans", "prefs")
LIMIT_TEXT = (
    "I can't answer that yet: I only use the facts shown in the app and what you saved in "
    "your account. Try: "
)
STATUS_TEXT = {
    "compatible": "Confirmed by the source",
    "incompatible": "Not compatible",
    "may_contain": "May contain (traces)",
    "unknown": "Cannot be confirmed",
    "contains": "Contains",
    "not_declared": "Not declared",
}
PROCESSING = {
    1: "unprocessed or minimally processed",
    2: "processed culinary ingredient",
    3: "processed food",
    4: "ultra-processed",
}
VALUE_NAMES = [
    ("energy-kcal", "Energy"),
    ("sugars", "Sugars"),
    ("saturated-fat", "Saturates"),
    ("salt", "Salt"),
    ("proteins", "Protein"),
    ("fiber", "Fibre"),
]

_RULES = [
    ("indicators", re.compile(r"nutri|nova|score|grade")),
    ("values", re.compile(r"sugar|salt|fat|protein|fibre|fiber|calor|energy|value")),
    ("remember", re.compile(r"remember|memor")),
    ("avoid", re.compile(r"avoid")),
    ("scans", re.compile(r"scan")),
    ("restrictions", re.compile(r"allerg|vegan|vegetarian|gluten|lactose|pork|restriction|fit")),
    ("prefs", re.compile(r"preference|goal")),
]


# ---------- Contexts ----------
def product_id(context: str) -> uuid.UUID | None:
    """The product of a ``product:<uuid>`` context; None for ``personal``.
    Raises ValueError for anything else."""
    if context == PERSONAL:
        return None
    prefix, _, raw = (context or "").partition(":")
    if prefix != "product":
        raise ValueError("unknown chat context")
    return uuid.UUID(raw)


def context_for(product) -> str:
    return f"product:{product.pk}" if product is not None else PERSONAL


def suggestions_for(context: str) -> list[tuple[str, str]]:
    return SUGGESTIONS["personal" if context == PERSONAL else "product"]


# ---------- Intent ----------
def detect_intent(text: str) -> str | None:
    t = (text or "").lower()
    for intent, pattern in _RULES:
        if pattern.search(t):
            return intent
    return None


# ---------- Facts ----------
def product_payload(product) -> dict[str, Any]:
    """The same payload the product page uses (values, restriction states, indicators)."""
    from catalog import presenters
    from myola.page import facts_from_product, ola_payload

    snapshots = list(product.current_revision.assessments.all())
    by_key = {i.key: i for i in presenters.indicator_views(product, snapshots)}
    nutri, nova = by_key.get("nutriscore"), by_key.get("nova")
    try:
        nova_group = int(nova.badge) if nova and nova.badge else None
    except (TypeError, ValueError):
        nova_group = None
    return ola_payload(
        facts_from_product(product, snapshots),
        nutriscore=(nutri.badge if nutri else "") or "",
        nova=nova_group,
    )


def _relevant_notes(user, product, question: str) -> list[str]:
    words = [w for w in re.split(r"\W+", (question or "").lower()) if len(w) >= 4]
    out = []
    for note in Note.objects.filter(user=user).select_related("product")[:300]:
        same_product = product is not None and note.product_id == product.pk
        text = note.text.lower()
        if same_product or any(w in text for w in words):
            out.append(note.text)
        if len(out) >= 2:
            break
    return out


def build_facts(user, context: str, product=None, question: str = "") -> dict[str, Any]:
    """Everything an answer may use, as plain data (safe to hand to an AI provider later)."""
    profile = get_profile(user)
    prefs = derive_prefs(profile)
    avoid = (
        ProductMark.objects.filter(user=user, kind=ProductMark.Kind.AVOID)
        .select_related("product")
        .order_by("-created_at")
    )
    scans = lists.recent(user, scans_only=True, limit=5)
    return {
        "context": context,
        "product": product_payload(product) if product is not None else None,
        "goal": str(P.GOALS[profile.goal][0]) if profile.goal in P.GOALS else "",
        "diets": [(d, str(P.DIETS[d][0])) for d in prefs["diets"]],
        "allergens": [(a, str(P.ALLERGENS[a])) for a in prefs["allergens"]],
        "memories": list(used_in_replies(user).values_list("text", flat=True)),
        "notes": _relevant_notes(user, product, question) if profile.use_notes else [],
        "avoid": [m.product.name or "Unnamed product" for m in avoid],
        "scans": [
            (v.product.name or "Unnamed product", date_format(v.created_at, "j M")) for v in scans
        ],
    }


# ---------- Wording ----------
def _quote(s: str) -> str:
    return f"“{s}”"


def _prefs_summary(facts: dict) -> str:
    parts = ([facts["goal"]] if facts["goal"] else []) + [label for _, label in facts["diets"]]
    parts += [f"allergy: {label}" for _, label in facts["allergens"]]
    return ", ".join(parts)


def rule_answer(intent: str | None, facts: dict[str, Any]) -> str:
    p = facts.get("product")
    lines: list[str] = []
    if intent == "indicators":
        if not p:
            lines.append("Open a product and ask there: I explain that product's indicators.")
        else:
            lines.append(
                f"Nutri-Score {p['nutriscore'].upper()}, as published by Open Food Facts (A is "
                "the most favourable, E the least), computed per 100 g or 100 ml."
                if p.get("nutriscore")
                else "The source gives no Nutri-Score for this product."
            )
            lines.append(
                f"Processing: {PROCESSING[p['nova']]}, judged from the ingredient list. It says "
                "nothing about sugar, fat or salt."
                if p.get("nova") in PROCESSING
                else "The source does not say how processed it is."
            )
    elif intent == "values":
        if not p:
            lines.append("Open a product and ask there: I read that product's label values.")
        else:
            values = p["facts"]["values"]
            known = [f"{label} {values[k]['display']}" for k, label in VALUE_NAMES if k in values]
            unknown = [label.lower() for k, label in VALUE_NAMES if k not in values]
            if not known:
                lines.append("No values per 100 g or 100 ml are recorded for this product.")
            else:
                basis = p["facts"]["basis_label"] or "per 100 g or ml"
                lines.append(f"{basis[0].upper()}{basis[1:]}: {'; '.join(known)}.")
                if unknown:
                    lines.append(f"Not recorded: {', '.join(unknown)}.")
    elif intent == "restrictions":
        if not p:
            lines.append(
                "Open a product and ask there: I check its recorded allergens and diet information."
            )
        elif not facts["diets"] and not facts["allergens"]:
            lines.append(
                "You haven't chosen any diet or allergy. You can set them in My Track → Profile."
            )
        else:
            states = p["restrictions"]
            for key, label in facts["diets"]:
                s = states["diets"].get(key, {})
                status = STATUS_TEXT.get(s.get("status", ""), s.get("status", ""))
                lines.append(f"{label}: {status}. {s.get('reason', '')}".strip())
            for key, label in facts["allergens"]:
                s = states["allergens"].get(key, {})
                status = STATUS_TEXT.get(s.get("status", ""), s.get("status", ""))
                lines.append(f"{label}: {status}. {s.get('reason', '')}".strip())
            lines.append("This is not a guarantee: always check the physical label.")
    elif intent == "remember":
        if facts["memories"]:
            lines.append(
                f"You asked me to remember: {'; '.join(_quote(m) for m in facts['memories'])}."
            )
        else:
            lines.append(
                "I don't remember anything yet. You can add or confirm memories in "
                "My Track → Notes → Foodo remembers."
            )
        summary = _prefs_summary(facts)
        if summary:
            lines.append(f"Your preferences: {summary}.")
    elif intent == "avoid":
        lines.append(
            f"You marked as Avoid: {', '.join(facts['avoid'])}."
            if facts["avoid"]
            else "You haven't marked any product as Avoid."
        )
    elif intent == "scans":
        lines.append(
            "Your recent scans: "
            + ", ".join(f"{name} ({day})" for name, day in facts["scans"])
            + ". A scan is not a purchase."
            if facts["scans"]
            else "No scans recorded yet."
        )
    elif intent == "prefs":
        summary = _prefs_summary(facts)
        lines.append(
            f"Your preferences: {summary}." if summary else "You haven't set any preferences yet."
        )
    else:
        options = ", ".join(_quote(label) for _, label in suggestions_for(facts["context"]))
        return f"{LIMIT_TEXT}{options}."

    if facts["notes"]:
        lines.append(f"From your notes: {'; '.join(_quote(n) for n in facts['notes'])}.")
    return "\n".join(lines)


def answer(user, context: str, text: str, product=None) -> str:
    """The rule-based answer, rephrased by the AI provider when it is enabled and within limits.

    Python decides every fact (values, restriction states, score); the model only phrases them.
    Any AI problem (off, limits, errors) silently falls back to the rule-based answer.
    """
    facts = build_facts(user, context, product, question=text)
    baseline = rule_answer(detect_intent(text), facts)
    from ai.service import phrase

    ai_facts = {**facts, "baseline_answer": baseline}
    if product is not None:
        from assessments.score import score_for_product

        ai_facts["foodo_score"] = score_for_product(product)
    recent = [
        {"role": "user" if m.role == ChatMessage.Role.USER else "assistant", "content": m.text}
        for m in history(user, context).order_by("-created_at", "-pk")[:6]
    ][::-1]
    return phrase(user, ai_facts, text, recent) or baseline


# ---------- History ----------
def history(user, context: str):
    return ChatMessage.objects.filter(user=user, context=context).order_by("created_at", "pk")


def send(user, context: str, text: str, product=None) -> tuple[ChatMessage, ChatMessage]:
    """Answer a question and keep both messages (the oldest beyond the limit are dropped).
    The answer (possibly a network call to the AI provider) is made outside the transaction."""
    text = (text or "").strip()[:300]
    reply = answer(user, context, text, product)
    return _store(user, context, text, reply)


@transaction.atomic
def _store(user, context: str, text: str, reply: str) -> tuple[ChatMessage, ChatMessage]:
    asked = ChatMessage.objects.create(
        user=user, context=context, role=ChatMessage.Role.USER, text=text
    )
    answered = ChatMessage.objects.create(
        user=user, context=context, role=ChatMessage.Role.OLA, text=reply
    )
    stale = list(
        ChatMessage.objects.filter(user=user, context=context)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)[LIMITS["chat_messages"] :]
    )
    if stale:
        ChatMessage.objects.filter(pk__in=stale).delete()
    return asked, answered


def clear(user) -> int:
    """Delete the chat history of every context."""
    return ChatMessage.objects.filter(user=user).delete()[0]

"""Ask Foodo (MVP): deterministic answers built only from server data.

Three steps, so that an AI provider can later replace only the wording:

* ``detect_intent(text)`` picks one of the known intents (or None);
* ``build_facts(user, context, product)`` collects the facts the answer may use: the product's
  recorded values, Foodo Score, processing level and restriction states, the user's preferences,
  lists and scans, confirmed and active memories, and notes only when the user allows it;
* ``rule_answer(intent, facts)`` phrases the answer from those facts alone.

It never invents values, ingredients, effects, availability or compatibility, and gives no
medical advice. Messages are kept per context (``personal`` or ``product:<uuid>``).
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
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
        ("indicators", "What do the Foodo Score and processing mean?"),
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
    "I can't answer that yet: I only use the facts shown in the app, Open Food Facts and what "
    "you saved in your account. You can also ask me to find a product by name or barcode. Try: "
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
    # Allergy and diet questions are always answered by the rules, never by the AI provider,
    # whatever the language: English plus Romanian, French, Spanish, Italian, German, Portuguese
    # and Polish stems (alergie, allergène, alérgeno, Allergie, alergia, orzechy...).
    (
        "restrictions",
        re.compile(
            r"allerg|alerg|alérg|vegan|vegetar|végé|gluten|glutine|glúten|lactos|lactoz|"
            r"laktos|lattos|pork|porc|cerdo|maiale|schwein|wieprz|halal|kosher|cușer|celiac|"
            r"coeliac|zöliak|intoleran|unverträg|nietoleran|nut|nuci|arahid|peanut|cacahu|"
            r"arachid|erdnuss|orzech|amendo|restriction|restricți|fit"
        ),
    ),
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
    payload = product_payload(product) if product is not None else None
    if payload is not None:
        from assessments.score import score_for_product, summary

        # Answers about the score use the Foodo Score (x/100), never the Nutri-Score letter.
        payload["foodo"] = summary(score_for_product(product))
    return {
        "context": context,
        "product": payload,
        "energy_visible": profile.energy_visible,
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
            foodo = p.get("foodo") or {}
            lines.append(
                f"Foodo Score {foodo['foodo_score']}: AskFoodo's conversion of the official "
                "Nutri-Score from Open Food Facts to a 0-100 scale (100 is the most "
                "favourable), per 100 g or 100 ml."
                if foodo.get("foodo_score")
                else "No Foodo Score for this product: "
                f"{(foodo.get('message') or 'not enough data').lower()}."
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
            names = [
                (k, label)
                for k, label in VALUE_NAMES
                if k != "energy-kcal" or facts.get("energy_visible", True)
            ]
            known = [f"{label} {values[k]['display']}" for k, label in names if k in values]
            unknown = [label.lower() for k, label in names if k not in values]
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
                "You haven't chosen any diet or allergy. You can set them in My account → Profile."
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
                "My account → Notes → Foodo remembers."
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


# Intents answered only by the rules, never by the AI provider: allergen/diet checks (a
# product name or ingredient list must never be able to talk the model into "safe") and
# everything about the user's own data (lists, scans, memories, preferences).
RULE_ONLY_INTENTS = frozenset({"restrictions", "remember", "avoid", "scans", "prefs"})


MAX_INGREDIENTS_CHARS = 1500  # long lists are cut (about 400 tokens at most)


def _ingredient_facts(product) -> dict[str, Any]:
    """Richer answers: the source's ingredient text and the additives identified in it."""
    from catalog.presenters import additive_label

    revision = product.current_revision
    text = (((revision.data or {}).get("ingredients") or {}).get("text") or "").strip()
    additives = next(
        (
            s
            for s in revision.assessments.all()
            if s.indicator == "additives"
            and s.origin == "off_computed"
            and s.data_status == "available"
        ),
        None,
    )
    tags = ((additives.value or {}).get("tags") or []) if additives else None
    return {
        "ingredients": text[:MAX_INGREDIENTS_CHARS] if text else "unknown",
        "additives": [additive_label(t) for t in tags] if tags is not None else "unknown",
    }


def _levels(product) -> dict[str, str]:
    """UK front-of-pack levels (low / moderate / high per 100 g or ml) from the source, as the
    product page shows them."""
    snap = next(
        (
            s
            for s in product.current_revision.assessments.all()
            if s.indicator == "nutrient_levels"
            and s.data_status == "available"
            and not s.invalidated_by_local_change
        ),
        None,
    )
    levels = ((snap.value or {}).get("levels") or {}) if snap else {}
    return {k: v for k, v in levels.items() if v in ("low", "moderate", "high")}


def ai_facts_for(
    facts: dict[str, Any], product, *, with_preferences: bool, with_ingredients: bool = False
) -> dict[str, Any]:
    """The allow-list of what may reach the AI provider: label facts of the product, its score,
    and (only with the user's opt-in) the names of their diets and allergies."""
    p = facts.get("product") or {}
    values = dict((p.get("facts") or {}).get("values") or {})
    if not facts.get("energy_visible", True):
        values.pop("energy-kcal", None)
    out: dict[str, Any] = {
        "product": {
            "name": (p.get("product") or {}).get("name", ""),
            "brand": (p.get("product") or {}).get("brand", ""),
            "quantity": (p.get("product") or {}).get("quantity", ""),
            "basis": (p.get("facts") or {}).get("basis_label", ""),
            "values": {k: v.get("display") for k, v in values.items()},
            "nova": p.get("nova"),
        }
    }
    if p.get("nova") in PROCESSING:
        out["product"]["processing"] = PROCESSING[p["nova"]]
    if product is not None:
        levels = _levels(product)
        if levels:
            out["product"]["levels"] = levels
        # Computed by code (score.v3, x/100); the AI only rephrases it, never a letter.
        if p.get("foodo") is not None:
            out["foodo_score"] = p["foodo"]
        if with_ingredients:
            out["product"].update(_ingredient_facts(product))
    if with_preferences:
        out["diets"] = [label for _, label in facts.get("diets", [])]
        out["allergies"] = [label for _, label in facts.get("allergens", [])]
    return out


# The very last fallback: shown only if every other answer came back blank.
NO_ANSWER = (
    "Sorry, I couldn't put an answer together just now. The label details are on the product "
    "page; you can also ask about its sugar, salt, fat, Foodo Score or processing."
)


def answer(user, context: str, text: str, product=None) -> str:
    """Never blank: the AI answer, else the rule-based one, else ``NO_ANSWER``."""
    return reply(user, context, text, product)[0]


def reply(user, context: str, text: str, product=None):
    """``(answer, AIAnswer or None)``: the answer is never blank."""
    return reply_with_sources(user, context, text, product)[:2]


def reply_with_sources(user, context: str, text: str, product=None):
    """``(answer, AIAnswer or None, sources)``. ``sources``: the products and Magazine articles
    an answer with retrieval used (``myola.services.rag``), as ``{kind, title, url}``."""
    return reply_full(user, context, text, product)[:3]


def reply_full(user, context: str, text: str, product=None):
    """``(answer, AIAnswer or None, sources, details)``. ``details``: what an answer with
    retrieval was built from (internal: criterion, market, category, candidates, exclusions,
    product values); a follow-up such as "and for 200 g?" reads it. Never shown."""
    sources: list[dict[str, str]] = []
    details: dict = {}
    out, log = _answer(user, context, text, product, sources, details)
    out = (out or "").strip()
    return (out or NO_ANSWER), log, (sources if out else []), (details if out else {})


# Follow-up questions ("and salt?"): the last AI answers of the same chat go with the question
# (only questions and AI answers, which never contain notes or memories).
FOLLOWUP_PAIRS = 2
FOLLOWUP_MINUTES = 30


def _followups(user, context: str) -> list[dict[str, str]]:
    from ai.models import AIAnswer

    rows = list(
        AIAnswer.objects.filter(
            user=user,
            context=context,
            outcome=AIAnswer.Outcome.ANSWERED,
            created_at__gte=timezone.now() - timedelta(minutes=FOLLOWUP_MINUTES),
        )
        .exclude(answer="")  # personal answers are not kept, so they cannot be followed up
        .exclude(kind=AIAnswer.Kind.TRANSLATION)
        .order_by("-created_at")[:FOLLOWUP_PAIRS]
    )
    turns: list[dict[str, str]] = []
    for row in reversed(rows):
        turns += [
            {"role": "user", "content": row.question},
            {"role": "assistant", "content": row.answer},
        ]
    return turns


def _answer(
    user, context: str, text: str, product=None, sources: list | None = None,
    details: dict | None = None,
):  # fmt: skip
    """``(answer, AIAnswer or None)``. The rule-based answer; for questions about a product's
    label (and general food questions in the general chat) the AI provider may answer from an
    allow-list of facts (see ``ai_facts_for``). Any AI problem (off, limits, errors, a blocked
    answer) silently falls back to the rule-based answer, and the model may hand a question
    about allergies, diets or the user's own data back to the rules.

    Every answer is in the language of the question: the rule answers are written in English,
    so they are translated (``_localise``); English with a short note only when that fails."""
    from ai import language
    from myola.services import chat_products

    lang = language.detect(text)
    profile = get_profile(user)

    def localise(answer: str, log=None, *, kind: str = "public"):
        return _localise(user, profile, answer, log, lang, text, context, kind=kind)

    sources = sources if sources is not None else []
    details = details if details is not None else {}
    found = chat_products.answer(text)
    if found:
        # Product search: facts only, never phrased by the AI provider (only translated).
        return localise(found)
    from myola.services import rag_text

    # Answers written from data are written in English or Romanian directly; other languages
    # get the English text translated like the rule answers.
    written = rag_text.lang_of(lang)

    def deliver(answer: str, log=None):
        return (answer, log) if not lang or lang == written else localise(answer, log)

    data_answer = _data_answer(user, context, text, product, sources, details, written)
    if data_answer is not None:
        return deliver(data_answer)
    intent = detect_intent(text)
    if intent == "scans":
        from myola.services.rag import criterion_of

        if criterion_of(text) is not None:
            intent = None  # "than the scanned product": a comparison, not the user's scans
    # Questions about the user, with their consent: answered from their own data. An allergy
    # or diet check of a product always stays with the rules (safety).
    product_check = product is not None and intent == "restrictions"
    if profile.ai_use_my_data and not product_check and is_personal(text, intent):
        personal = _personal_answer(user, profile, context, text)
        if personal is not None:
            return personal
    facts = build_facts(user, context, product, question=text)
    baseline = rule_answer(intent, facts)
    kind = _personal_data(intent, facts)
    if intent in RULE_ONLY_INTENTS:
        return localise(baseline, kind=kind)
    with_retrieval = _rag_answer(
        user, profile, context, text, product, facts, intent, baseline, lang, localise,
        sources, details, deliver,
    )  # fmt: skip
    if with_retrieval is not None:
        return with_retrieval
    from ai.models import AIAnswer
    from ai.service import ask
    from staff.models import SiteSettings

    site = SiteSettings.load()
    from myola.services.rag import concept_of

    # A question about something labels do not record here (omega-3…) is a general question,
    # also on a product page: the product's label facts cannot answer it.
    if product is None or concept_of(text):
        # General chat: general food and nutrition questions may go to the AI (only the
        # question is sent). Not for people who told us about an eating disorder.
        if not site.ai_general_questions or profile.eating_disorder:
            return localise(baseline, kind=kind)
        result = ask(user, {}, text, _followups(user, context), general=True, context=context)
    else:
        ai_facts = ai_facts_for(
            facts,
            product,
            with_preferences=profile.ai_use_preferences,
            with_ingredients=site.ai_send_ingredients,
        )
        # Rule answers never go out: they can contain notes and memories.
        result = ask(user, ai_facts, text, _followups(user, context), context=context)
    if result.route:
        routed = rule_answer(result.route, facts)
        return localise(routed, result.log, kind=_personal_data(result.route, facts))
    if result.text:
        return result.text, result.log
    if result.log and result.log.outcome == AIAnswer.Outcome.FAILED:
        return _english_only(baseline, lang, "busy"), result.log  # the provider is failing now
    return localise(baseline, result.log, kind=kind)


def _data_answer(user, context, text, product, sources, details, lang="en") -> str | None:
    """Answers written only from the data, before anything else (English; translated by the
    caller): a follow-up asking for another amount ("Dar la 200 g?"), computed from the values
    of the previous answer of this chat; and a question about one value of the open product
    ("Câtă sare are?"). None for any other question."""
    from myola.services import rag

    amount = rag.amount_asked(text)
    if amount is not None:
        previous = (
            ChatMessage.objects.filter(user=user, context=context, role=ChatMessage.Role.OLA)
            .exclude(details={})
            .order_by("-created_at", "-pk")
            .values_list("details", flat=True)
            .first()
        )
        answer = rag.followup_answer(previous, *amount, lang)
        if previous and previous.get("kind") in ("comparison", "value", "recommendation"):
            details.update(previous)  # a further follow-up works from the same values
            for p in previous.get("products") or []:
                if p.get("value") is not None:
                    title = p["name"] + (f" ({p['brand']})" if p.get("brand") else "")
                    sources.append({"kind": "product", "title": title or "Unnamed product",
                                    "url": f"/p/{p['id']}/"})  # fmt: skip
        return answer
    if rag.wants_recommendation(text, product):
        # "Ce pește îmi recomanzi…", "Unde găsesc…": the whole catalogue, in any chat (also
        # one started from the diary); "these fats" is read from the user's earlier messages.
        earlier = list(
            ChatMessage.objects.filter(user=user, context=context, role=ChatMessage.Role.USER)
            .order_by("-created_at", "-pk")
            .values_list("text", flat=True)[:4]
        )
        answer, links, found = rag.recommend(text, earlier, user, lang)
        sources.extend(links)
        details.update(found)
        return answer
    if product is not None:
        nutrient = rag.nutrient_asked(text)
        if nutrient is not None:
            answer, found = rag.value_answer(product, nutrient, lang)
            details.update(found)
            sources.append({"kind": "product", "title": rag._name(product),
                            "url": rag._product_url(product)})  # fmt: skip
            return answer
    return None


def _rag_answer(user, profile, context, text, product, facts, intent, baseline, lang, localise,
                sources, details, deliver):  # fmt: skip
    """``(answer, log)`` with retrieval (``myola.services.rag``), or None when the question is
    neither a product comparison nor answered by a Magazine passage (the usual flow then).
    The links of the answer are added to ``sources``."""
    from ai.models import AIAnswer
    from ai.service import ask
    from core import passages
    from myola.services import rag
    from staff.models import SiteSettings

    site = SiteSettings.load()
    comparison = rag.compare_products(text, product, user)
    # The Magazine for product questions, and for general ones where general AI questions are
    # allowed (not for people who told us about an eating disorder).
    general_ok = site.ai_general_questions and not profile.eating_disorder
    if comparison is not None:
        details.update(comparison.details())
        return _comparison_answer(user, context, text, comparison, lang, deliver, sources, details)
    hits = passages.search(text, user=user) if (product is not None or general_ok) else []
    if not hits:
        return None
    product_facts = None
    if product is not None:
        product_facts = ai_facts_for(
            facts,
            product,
            with_preferences=profile.ai_use_preferences,
            with_ingredients=site.ai_send_ingredients,
        )
    material = rag.build_material(None, hits, product_facts)
    details.update(kind="magazine", passages=[h.passage.pk for h in hits])
    # No earlier answers as history: the model restated them (the repeated explanation).
    result = ask(user, material.facts, text, [], context=context, mode="rag")
    if result.route:
        routed = rule_answer(result.route, facts)
        return localise(routed, result.log, kind=_personal_data(result.route, facts))
    if result.text:
        clean, links = rag.cited_sources(result.text, material)
        sources.extend(links)
        return clean, result.log
    # No AI answer: the same material, phrased by code (with the rule answer when the question
    # was a known one about the product).
    fallback = rag.deterministic_answer(material)
    if intent is not None and product is not None:
        fallback = f"{baseline}\n{fallback}"
    clean, links = rag.cited_sources(fallback, material)
    sources.extend(links)
    if result.log and result.log.outcome == AIAnswer.Outcome.FAILED:
        return _english_only(clean, lang, "busy"), result.log
    return localise(clean, result.log)


def _comparison_answer(user, context, text, comparison, lang, deliver, sources, details):
    """A product comparison: the products and values come straight from the data
    (``rag.direct_answer``), written in the question's language (English or Romanian; other
    languages translated like rule answers), with links to every product compared. When the
    question also asks how or why (for example "and how do I compare the labels?"), the best
    Magazine passage is quoted after it, with its article linked; if the Magazine has nothing
    on it, one line says so. Nothing here is written by the AI."""
    from core import passages
    from myola.services import rag, rag_text

    written = rag_text.lang_of(lang)
    hits = []
    explain = rag.asks_explanation(text) and comparison.problem != "no_reference"
    if explain:
        hits = passages.search(text, user=user)[:1]
        details["passages"] = [h.passage.pk for h in hits]
    material = rag.build_material(comparison, hits)
    excerpts = _translated_excerpts(user, hits, written, text, context)
    answer = rag.deterministic_answer(material, written, excerpts)
    if explain and not hits:
        answer += "\n" + rag_text.t(written, "no_article")
    clean, links = rag.cited_sources(answer, material)
    sources.extend(links)
    return deliver(clean)


def _translated_excerpts(user, hits, lang, question, context) -> dict[int, str]:
    """The quoted passages in the answer's language: the articles are in English, so for a
    Romanian answer each quote is translated (translation only, nothing added); without AI
    the English words stay, marked as such."""
    from myola.services import rag

    if lang == "en" or not hits:
        return {}
    from ai.service import translate

    out = {}
    for i, hit in enumerate(hits, start=1):
        excerpt = rag._excerpt(hit.passage.text)
        done, _log = translate(user, excerpt, lang, question=question, context=context)
        out[i] = done or f"{excerpt} (în engleză)"
    return out


# "my", "I", "me" in the app's languages: a question about the user themselves.
_FIRST_PERSON = re.compile(
    r"\b(my|me|i|i'm|i've|i'd|mine|myself|meu|mea|mei|mele|eu|mi|[iî]mi|am|mine|"
    r"mio|mia|miei|mie|mon|ma|mes|je|j'ai|moi|mein|meine|meinen|ich|mir|m[oó]j|moje|moja|"
    r"yo|he|mis)\b",
    re.IGNORECASE,
)


def is_personal(text: str, intent: str | None) -> bool:
    """A question about the user's own data (diary, water, weight, lists, memories, …)."""
    from myola.context.django_source import sections_for, wants_products

    if intent in ("remember", "avoid", "scans", "prefs"):
        return True
    if not _FIRST_PERSON.search(text or ""):
        return False
    return bool(sections_for(text)) or wants_products(text)


def _personal_answer(user, profile, context: str, text: str):
    """``(answer, log)`` from the user's own data (``myola.context``), or None when the AI
    cannot answer now (the rules answer instead)."""
    from ai.service import ask
    from myola.context import get_source
    from myola.context.django_source import wants_products
    from myola.services.profiles import local_today

    source = get_source()
    facts = {"me": source.user_context(user, text, today=local_today(profile))}
    if wants_products(text):
        facts["available_products"] = source.available_products(user, query=text)
    own = (
        frozenset(int(v) for v in profile.targets.values() if v)
        if profile.show_targets
        else frozenset()
    )
    result = ask(
        user, facts, text, _followups(user, context), context=context, mode="personal",
        own_targets=own,
    )  # fmt: skip
    return (result.text, result.log) if result.text else None


def _personal_data(intent: str | None, facts: dict) -> str:
    """What of the user's own data a rule answer holds, for translating it (see the privacy
    page): "private" (notes, memories, lists, scans, goal: sent to the AI provider only with
    "Let Ask Foodo use my diary, lists, notes and memories" on), "diets" (diet and allergy
    names: with that switch or "Use my diets and allergies in AI answers") or "public"."""
    if facts.get("notes") or intent in ("remember", "avoid", "scans"):
        return "private"
    if intent == "prefs":
        return "private" if facts.get("goal") else "diets"
    if intent == "restrictions":
        return "diets"
    return "public"


def _english_only(answer: str, lang: str, why: str) -> str:
    from ai import language

    return language.english_only(answer, lang, why) if lang and lang != "en" else answer


def _localise(user, profile, answer, log, lang, question, context, *, kind):
    """A rule answer (English) in the question's language, translated by the AI provider.
    ``kind`` (``_personal_data``): "private" answers are sent only with the user's consent
    ("Let Ask Foodo use my diary, lists, notes and memories"); "diets" with that switch or
    "Use my diets and allergies in AI answers". Otherwise, or when the AI is off, out of
    messages or failing, the English answer with a one-line note in their language. Answers
    with the user's data are never kept in the log."""
    if not lang or lang == "en":
        return answer, log
    if kind == "private" and not profile.ai_use_my_data:
        return _english_only(answer, lang, "private"), log
    if kind == "diets" and not (profile.ai_use_preferences or profile.ai_use_my_data):
        return _english_only(answer, lang, "consent"), log
    from ai.service import translate

    private = kind != "public"
    out, tlog = translate(user, answer, lang, question=question, private=private, context=context)
    if out:
        return out, log or tlog
    return _english_only(answer, lang, "busy"), log or tlog


# ---------- History ----------
def history(user, context: str):
    return (
        ChatMessage.objects.filter(user=user, context=context)
        .select_related("ai_answer")
        .order_by("created_at", "pk")
    )


def send(user, context: str, text: str, product=None) -> tuple[ChatMessage, ChatMessage]:
    """Answer a question and keep both messages (the oldest beyond the limit are dropped).
    The answer (possibly a network call to the AI provider) is made outside the transaction."""
    text = (text or "").strip()[:300]
    out, log, sources, details = reply_full(user, context, text, product)
    return _store(user, context, text, out, log, sources, details)


@transaction.atomic
def _store(
    user, context: str, text: str, out: str, log=None, sources=None, details=None
) -> tuple[ChatMessage, ChatMessage]:
    asked = ChatMessage.objects.create(
        user=user, context=context, role=ChatMessage.Role.USER, text=text
    )
    answered = ChatMessage.objects.create(
        user=user,
        context=context,
        role=ChatMessage.Role.OLA,
        text=out,
        # Helpful / not helpful buttons only under answers the AI wrote.
        ai_answer=log if log is not None and log.outcome == log.Outcome.ANSWERED else None,
        sources=sources or [],
        details=details or {},
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

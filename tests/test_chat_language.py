"""One language rule for every Ask Foodo answer (ai.language.choose) and natural wording
(myola.services.chat_text). The checks are on facts (numbers, units, basis, links, the
language kept on the answer, no internal markers), never on one exact model sentence."""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from ai import language
from myola.models import ChatMessage
from myola.services import chat
from tests.test_rag import (
    QUESTION,
    UK,
    article,
    fishes,
    pizzas,
    provider,
    site,
)

MARKERS = re.compile(r"\[(?:P|A)\d+\]|\bmatches\b|scanned_product|saturated-fat|\bfat: ")


# ---------- The rule ----------
@pytest.mark.parametrize(
    ("text", "previous", "preference", "expected"),
    [
        ("Ce pizza are mai puțină sare? Answer in English please", None, "", ("en", "explicit")),
        ("răspunde-mi în engleză: ce pizza are mai puțină sare?", None, "", ("en", "explicit")),
        ("Which one has less salt? En français s'il vous plaît", None, "", ("fr", "explicit")),
        ("Ce pizza are mai puțină sare?", {"lang": "en", "lang_reason": "question"}, "",
         ("ro", "question")),
        ("200 g?", {"lang": "ro", "lang_reason": "question"}, "", ("ro", "conversation")),
        ("Which has less salt?", {"lang": "en", "lang_reason": "explicit"}, "",
         ("en", "explicit-kept")),
        ("Ce pizza are mai puțină sare?", {"lang": "en", "lang_reason": "explicit-kept"}, "",
         ("en", "explicit-kept")),
        ("200 g?", None, "ro", ("ro", "account")),
        ("200 g?", None, "", ("en", "interface")),
    ],
)  # fmt: skip
def test_choose_follows_one_order(text, previous, preference, expected):
    assert language.choose(text, previous=previous, preference=preference) == expected


# ---------- The same facts in several languages, without the AI ----------
def ask(member, ctx, text, product=None):
    return chat.send(member, ctx, text, product)[1]


def test_comparison_same_facts_in_english_and_romanian(member, pizzas, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    en = ask(member, ctx, "Which pizza has less salt than this?", pizzas["scanned"])
    ro = ask(member, ctx, "Ce pizza are mai puțină sare decât aceasta?", pizzas["scanned"])
    assert en.details["lang"] == "en" and ro.details["lang"] == "ro"
    assert "0.8 g" in en.text and "per 100 g" in en.text
    assert "0,8 g" in ro.text and "la 100 g" in ro.text and "Regatul Unit" in ro.text
    assert [s["url"] for s in en.sources] == [s["url"] for s in ro.sources]
    assert provider.calls == []  # both written by the app, no AI call
    for answer in (en, ro):
        assert not MARKERS.search(answer.text)


def test_follow_up_keeps_the_conversation_language_then_switches(member, pizzas, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    ask(member, ctx, "Ce pizza are mai puțină sare?", pizzas["scanned"])
    short = ask(member, ctx, "200 g?", pizzas["scanned"])
    assert short.details["lang"] == "ro" and short.details["lang_reason"] == "conversation"
    assert short.text.startswith("Sare în 200 g") and "1,6 g" in short.text
    # A clear question in another language switches the answer's language.
    switched = ask(member, ctx, "And for 300 g?", pizzas["scanned"])
    assert switched.details["lang"] == "en" and "2.4 g" in switched.text
    # An explicit request wins and is kept for the following messages.
    asked = ask(member, ctx, "Ce pizza are mai puțină sare? Răspunde în engleză.",
                pizzas["scanned"])  # fmt: skip
    assert asked.details["lang"] == "en" and asked.text.startswith("With less salt")
    kept = ask(member, ctx, "Dar la 200 g?", pizzas["scanned"])
    assert kept.details["lang_reason"] == "explicit-kept" and kept.text.startswith("Salt in 200 g")


def test_language_without_templates_uses_the_fallback_without_ai(member, pizzas, site, provider):
    site.ai_enabled = False
    site.save()
    out = ask(member, f"product:{pizzas['scanned'].pk}", "¿Qué pizza tiene menos sal?",
              pizzas["scanned"])  # fmt: skip
    assert out.details["lang"] == "es"
    assert out.text.startswith("Ahora mismo no puedo traducir esta respuesta")  # note, in Spanish
    assert "Low Salt Veggie (Test Pizzeria): 0.8 g" in out.text  # the facts, in English


def test_language_without_templates_is_phrased_once_by_the_ai(member, pizzas, provider):
    out = ask(member, f"product:{pizzas['scanned'].pk}", "¿Qué pizza tiene menos sal?",
              pizzas["scanned"])  # fmt: skip
    translations = [c for c in provider.calls if c["system"].startswith("You translate")]
    assert len(provider.calls) == len(translations) == 1  # one call, no extra rewrite
    assert "Spanish" in translations[0]["system"]
    assert "0.8 g" in out.text and len(out.sources) == 3


def test_romanian_question_about_uk_products(member, fishes, provider):
    out = ask(member, "personal", "Recomandă-mi un pește cu cele mai puține grăsimi")
    assert out.details["lang"] == "ro" and out.details["markets"] == [UK]
    assert "Regatul Unit" in out.text and "0,7 g" in out.text
    assert out.sources[0]["url"] == reverse("product", args=[fishes[2].pk])


def test_zero_matches_and_not_enough_data_read_naturally(member, pizzas, provider):
    none = ask(member, f"product:{pizzas['low'].pk}", "Ce pizza are mai puțină sare?",
               pizzas["low"])  # fmt: skip
    assert none.text.startswith("Nicio potrivire în catalogul verificat")
    assert "Am comparat 3 din 4 produse; unul nu are o valoare comparabilă" in none.text
    missing = ask(member, f"product:{pizzas['unknown'].pk}", "Ce pizza are mai puțină sare?",
                  pizzas["unknown"])  # fmt: skip
    assert missing.text == ("Eticheta acestui produs nu dă valoarea pentru sare la 100 g sau la "
                            "100 ml, așa că nu îl pot compara cu altele.")  # fmt: skip


def test_rule_answers_written_in_romanian_without_translation(member, pizzas, provider):
    out = ask(member, f"product:{pizzas['scanned'].pk}", "Care sunt valorile principale?",
              pizzas["scanned"])  # fmt: skip
    assert out.text.startswith("La 100 g: Energie 250 kcal; Zaharuri 3 g")
    assert "Sare 1,2 g" in out.text
    assert not [c for c in provider.calls if c["system"].startswith("You translate")]


def test_ai_gets_the_chosen_language_and_readable_levels(member, pizzas, provider, monkeypatch):
    from myola.services import chat as chat_module

    monkeypatch.setattr(chat_module, "_levels", lambda product: {"saturated-fat": "high",
                                                                  "salt": "moderate"})  # fmt: skip
    provider.text = "Da, are multe grăsimi saturate."
    ctx = f"product:{pizzas['scanned'].pk}"
    ask(member, "personal", "Hello there, what is fibre?")  # another chat, in English
    ask(member, ctx, "E o gustare bună?", pizzas["scanned"])
    call = provider.calls[-1]
    assert "Write the whole answer in Romanian" in call["system"]
    assert "Answer the question in the first sentence" in call["system"]
    facts = json.loads(call["messages"][-1]["content"].split("FACTS:\n")[1].split("\n\n")[0])
    assert facts["product"]["levels"] == ["high in saturated fat", "medium in salt"]


def test_answers_are_not_shared_between_users_or_languages(member, pizzas, provider):
    from django.contrib.auth import get_user_model

    other = get_user_model().objects.create_user("lang-other", password="a-long-password-77")
    ctx = f"product:{pizzas['scanned'].pk}"
    ro = ask(member, ctx, "Ce pizza are mai puțină sare?", pizzas["scanned"])
    en = chat.send(other, ctx, "Which pizza has less salt?", pizzas["scanned"])[1]
    assert ro.details["lang"] == "ro" and en.details["lang"] == "en"
    assert "0,8 g" in ro.text and "0.8 g" in en.text
    # The other user's short follow-up continues in their own conversation's language.
    follow = chat.send(other, ctx, "200 g?", pizzas["scanned"])[1]
    assert follow.details["lang"] == "en" and follow.text.startswith("Salt in 200 g")
    assert ChatMessage.objects.filter(user=other).count() == 4


def test_explanation_from_an_english_article_in_romanian_without_ai(member, pizzas, article, site,
                                                                    provider):  # fmt: skip
    site.ai_enabled = False
    site.save()
    out = ask(member, f"product:{pizzas['scanned'].pk}",
              "Ce pizza are mai puțină sare și how do I compare the labels per 100 g?",
              pizzas["scanned"])  # fmt: skip
    if any(s["kind"] == "article" for s in out.sources):
        assert "(citat în engleză)" in out.text or out.details["lang"] == "en"
    assert not MARKERS.search(out.text)

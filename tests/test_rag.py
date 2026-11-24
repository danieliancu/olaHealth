"""Ask Foodo with retrieval (myola/services/rag.py, core/passages.py): comparisons computed in
code from current values, Magazine passages of published articles only, links built from what
was selected. The AI provider is simulated (FakeProvider); no network."""

from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.urls import reverse

from ai import embeddings, service
from ai.providers.base import ProviderError, Reply
from catalog.models import NutrientFact
from catalog.services.importer import import_off_product
from core import passages
from core.models import ArticlePassage, LearnArticle, LearnTopic
from myola.models import ChatMessage, ProductMark
from myola.services import chat, rag
from tests.nutri import nutriscore_fields

UK = "en:united-kingdom"
PIZZAS = ["en:meals", "en:pizzas-pies-and-quiches", "en:pizzas"]
QUESTION = "Ce pizza din UK are mai puțină sare decât produsul scanat și cum compar etichetele?"
HX = {"HTTP_HX_REQUEST": "true"}


class ScriptedProvider:
    """Answers with a fixed text and records what it was sent."""

    name = "fake"

    def __init__(self, text: str = "", *, fail: bool = False):
        self.model = "claude-haiku-5-5"
        self.text, self.fail, self.calls = text, fail, []

    def reply(self, system, messages, max_tokens, *, short_thinking=False):
        self.calls.append({"system": system, "messages": messages})
        if self.fail:
            raise ProviderError("down")
        if system.startswith("You translate"):  # translations keep the text (simulated)
            text = messages[-1]["content"]
            return Reply(text=text, input_tokens=10, output_tokens=10, model=self.model)
        return Reply(text=self.text, input_tokens=100, output_tokens=50, model=self.model)

    def facts(self, n: int = -1) -> dict:
        content = self.calls[n]["messages"][-1]["content"]
        return json.loads(content.split("FACTS:\n", 1)[1].split("\n\nQUESTION:", 1)[0])


@pytest.fixture
def site(db):
    from staff.models import SiteSettings

    s = SiteSettings.load()
    s.ai_enabled = True
    s.ai_provider = "anthropic"
    s.ai_model = "claude-haiku-5-5"
    s.ai_daily_messages_per_user = 20
    s.ai_monthly_budget_gbp = Decimal("15.00")
    s.ai_general_questions = True
    s.save()
    return s


@pytest.fixture
def provider(monkeypatch, site):
    p = ScriptedProvider()
    monkeypatch.setattr(service, "get_provider", lambda name, model="": p)
    return p


def pizza(off_payload, code, name, salt, *, countries=(UK,), grade="c", **changes):
    nutriments = {"energy-kcal_100g": 250, "sugars_100g": 3, "saturated-fat_100g": 4}
    if salt is not None:
        nutriments["salt_100g"] = salt
    payload = off_payload(
        code=code,
        product_name=name,
        brands="Test Pizzeria",
        categories_tags=PIZZAS,
        countries_tags=list(countries),
        nutriments=nutriments,
        nutrient_levels={},
        **nutriscore_fields(grade),
        **changes,
    )
    return import_off_product(payload)


@pytest.fixture
def pizzas(db, off_payload):
    cache.clear()
    return {
        "scanned": pizza(off_payload, "5000000000101", "Scanned Margherita", 1.2),
        "low": pizza(off_payload, "5000000000102", "Low Salt Veggie", 0.8),
        "mid": pizza(off_payload, "5000000000103", "Pepperoni", 1.0),
        "high": pizza(off_payload, "5000000000104", "Four Cheese", 1.5),
        "unknown": pizza(off_payload, "5000000000105", "Mystery Pizza", None),
        "france": pizza(off_payload, "5000000000106", "Pizza Reine", 0.5, countries=("en:france",)),
    }


@pytest.fixture
def article(db):
    topic = LearnTopic.objects.create(key="labels-test", title="Labels")
    return LearnArticle.objects.create(
        slug="compare-labels",
        topic=topic,
        title="How to compare two labels",
        summary="Compare products on the same basis.",
        body=(
            "## Same basis\n\nAlways compare salt per 100 g with salt per 100 g, never per "
            "serving, because portion sizes differ.\n\n## Salt\n\nA salt value above 1.5 g "
            "per 100 g is high in the UK front-of-pack scheme."
        ),
    )


# ---------- Reading the question ----------
def test_criteria_are_distinct_and_multilingual():
    assert rag.criterion_of(QUESTION) == rag.Criterion("salt", "lower")
    assert rag.criterion_of("Which pizza has less salt than this?") == rag.Criterion(
        "salt", "lower"
    )
    assert rag.criterion_of("Which pizza has a higher Foodo Score than this one?") == (
        rag.Criterion("foodo", "higher")
    )
    # Naming a nutrient compares that nutrient, never the score.
    assert rag.criterion_of("Which has less salt and a better score than this?").nutrient == "salt"
    assert rag.criterion_of("Which has less saturated fat than this?").nutrient == "saturated-fat"
    assert rag.criterion_of("What is salt?") is None
    assert rag.market_of(QUESTION) == UK
    assert rag.market_of("pizza din Regatul Unit") == UK
    assert rag.market_of("pizza made in Luxembourg") is None


# ---------- Comparison in code ----------
def test_only_products_that_meet_the_criterion_with_db_values(pizzas, member):
    c = rag.compare_products(QUESTION, pizzas["scanned"], member)
    assert c.problem == "" and c.category == "en:pizzas" and c.markets == [UK]
    assert c.reference_value == Decimal("1.2") and c.basis == "per_100g" and c.unit == "g"
    assert [m.product for m in c.matches] == [pizzas["low"], pizzas["mid"]]
    for m in c.matches:  # every match meets the criterion, with the value stored in the DB
        stored = NutrientFact.objects.get(
            revision=m.product.current_revision, nutrient="salt", basis="per_100g",
            preparation="as_sold",
        )  # fmt: skip
        assert m.value == stored.value < c.reference_value
        assert m.difference == m.value - c.reference_value
    # France is another market; the product without a salt value is unknown, not "0 g".
    assert c.candidates == 4 and c.unknown == 1
    assert pizzas["france"] not in [m.product for m in c.matches]


def test_current_values_include_approved_corrections(pizzas, member):
    from django.contrib.auth import get_user_model

    from contributions.models import Contribution
    from contributions.services.moderation import approve

    high = pizzas["high"]
    rev = high.current_revision
    nutrition = copy.deepcopy(rev.data["nutrition"])
    for f in nutrition["facts"]:
        if f["nutrient"] == "salt":
            f["value"] = "0.6"
    moderator = get_user_model().objects.create_user("mod", is_staff=True, is_superuser=True)
    c = Contribution.objects.create(
        product=high, base_revision=rev, problem_type=Contribution.ProblemType.INCORRECT_DATA,
        proposed={"nutrition": nutrition}, status=Contribution.Status.SUBMITTED,
        consent_publication=True,
    )  # fmt: skip
    assert approve(c.pk, moderator, "label photo").status == "approved"
    result = rag.compare_products(QUESTION, pizzas["scanned"], member)
    assert result.matches[0].product == high and result.matches[0].value == Decimal("0.6")


def test_foodo_score_is_another_criterion(pizzas, off_payload, member):
    # Lower salt but a worse Nutri-Score: a salt match, never a Foodo Score match.
    worse = pizza(off_payload, "5000000000107", "Salty-free but sweet", 0.7, grade="e")
    salt = rag.compare_products("Which pizza has less salt than this?", pizzas["scanned"], member)
    assert worse in [m.product for m in salt.matches]
    score = rag.compare_products(
        "Which pizza has a higher Foodo Score than this?", pizzas["scanned"], member
    )
    assert worse not in [m.product for m in score.matches]
    assert all(m.foodo > score.reference_foodo for m in score.matches)


def test_missing_reference_value_and_no_alternative(pizzas, member):
    c = rag.compare_products(QUESTION, pizzas["unknown"], member)
    assert c.problem == "no_reference_value" and c.matches == []
    assert rag.direct_answer(c) == (
        "This product's label gives no salt value per 100 g or 100 ml, so I can't compare it "
        "with others."
    )
    lowest = rag.compare_products(QUESTION, pizzas["low"], member)
    assert lowest.matches == [] and lowest.problem == ""
    assert rag.direct_answer(lowest) == (
        "No match in the verified catalogue: no other product in the “Pizzas” category sold in "
        "the UK has less salt than this one (0.8 g per 100 g). Compared 3 of 4 products; 1 has "
        "no comparable salt value."
    )


def test_market_is_where_the_scanned_product_is_sold(db, off_payload, member):
    """Not the user's market: a pizza sold in Belgium and the Netherlands is compared with
    pizzas sold there (the error behind "no other pizza" for an Albert Heijn pizza)."""
    cache.clear()
    be_nl = ("en:belgium", "en:netherlands")
    scanned = pizza(off_payload, "5000000000301", "Shoarma pizza", 1.0, countries=be_nl)
    pizza(off_payload, "5000000000302", "Quattro formaggi", 1.01, countries=("en:netherlands",))
    pizza(off_payload, "5000000000303", "UK low salt", 0.5)  # UK only: another market
    c = rag.compare_products("Ce altă pizza are mai puțină sare?", scanned, member)
    assert c.markets == list(be_nl) and c.market_source == "product"
    assert c.candidates == 1 and c.matches == []
    assert "sold in Belgium or the Netherlands" in rag.direct_answer(c)
    # Named in the question, the market wins.
    named = rag.compare_products("Which UK pizza has less salt than this?", scanned, member)
    assert named.markets == [UK] and [m.product.name for m in named.matches] == ["UK low salt"]


def generic(off_payload, code, name, category_tags, countries, **nutriments):
    payload = off_payload(
        code=code, product_name=name, brands="Brand", categories_tags=category_tags,
        countries_tags=list(countries), nutriments=nutriments, nutrient_levels={},
        **nutriscore_fields("c"),
    )  # fmt: skip
    return import_off_product(payload)


@pytest.mark.parametrize(
    ("tags", "question", "nutrient", "ref", "better", "worse"),
    [
        (["en:dairies", "en:yogurts"], "Ce alt iaurt are mai puțin zahăr?", "sugars_100g",
         8, 4, 12),
        (["en:breakfasts", "en:breakfast-cereals"], "Ce alte cereale au mai multe fibre?",
         "fiber_100g", 5, 9, 2),
        (PIZZAS, "Ce altă pizza are mai puțină sare?", "salt_100g", 1.2, 0.7, 1.6),
    ],
)  # fmt: skip
def test_three_situations_per_category(db, off_payload, member, tags, question, nutrient, ref,
                                       better, worse):  # fmt: skip
    """For each category: an alternative exists, none meets the criterion, data missing."""
    cache.clear()
    ro = ("en:romania",)
    base = {"energy-kcal_100g": 100}

    def make(code, name, **values):
        return generic(off_payload, code, name, tags, ro, **base, **values)

    scanned = make("5000000000401", "Scanned", **{nutrient: ref})
    good = make("5000000000402", "Better one", **{nutrient: better})
    make("5000000000403", "Worse one", **{nutrient: worse})
    make("5000000000404", "No value")
    found = rag.compare_products(question, scanned, member)
    assert [m.product for m in found.matches] == [good]
    text = rag.direct_answer(found)
    assert f"Better one (Brand): {rag._amount(Decimal(str(better)), 'g')}" in text
    assert "sold in Romania" in text and "Compared 2 of 3 products; 1 has no" in text
    # None meets it: the best product itself.
    none = rag.compare_products(question, good, member)
    assert none.matches == [] and rag.direct_answer(none).startswith(
        "No match in the verified catalogue"
    )
    # Data missing on the scanned product.
    gap = rag.compare_products(question, make("5000000000405", "Unlabelled"), member)
    assert gap.problem == "no_reference_value" and "gives no" in rag.direct_answer(gap)
    for answer in (text, rag.direct_answer(none), rag.direct_answer(gap)):
        assert "[P" not in answer and "matches" not in answer


def test_avoid_list_is_respected(pizzas, member):
    ProductMark.objects.create(user=member, product=pizzas["low"], kind=ProductMark.Kind.AVOID)
    c = rag.compare_products(QUESTION, pizzas["scanned"], member)
    assert [m.product for m in c.matches] == [pizzas["mid"]]


# ---------- Magazine passages ----------
def test_passages_follow_the_article(article):
    rows = list(ArticlePassage.objects.filter(article=article))
    assert [r.heading for r in rows] == ["", "Same basis", "Salt"]
    assert all(r.version == passages.version_of(article) for r in rows)
    hits = passages.search("compare salt per 100 g")
    assert hits and hits[0].article == article and hits[0].method == "fulltext"
    # Edited: rebuilt at once, the old text is gone.
    article.body = "## Fibre\n\nFibre helps digestion."
    article.save()
    assert list(ArticlePassage.objects.filter(article=article).values_list("heading", flat=True))[
        1:
    ] == ["Fibre"]
    assert passages.search("salt per 100 g portion sizes") == []
    # Unpublished: excluded at once, also from a search cached before.
    assert passages.search("fibre digestion")
    article.published = False
    article.save()
    assert passages.search("fibre digestion") == []
    assert not ArticlePassage.objects.filter(article=article).exists()


def test_withdrawn_article_never_returned_from_cache(article):
    assert passages.search("portion sizes differ")
    LearnArticle.objects.filter(pk=article.pk).update(published=False)  # no signal at all
    assert passages.search("portion sizes differ") == []


def test_rebuild_from_local_content(article):
    ArticlePassage.objects.all().delete()
    report = passages.rebuild()
    assert report["passages"] == ArticlePassage.objects.count() >= 3
    assert report["without_embedding"] == report["passages"]  # no embeddings in tests


def test_search_by_meaning_with_other_words(article, site, monkeypatch):
    """With embeddings, a question in other words (and another language) finds the passage."""
    groups = [("salt", "sodium", "sare", "sodiu"), ("basis", "serving", "same", "aceeași"),
              ("fibre", "digestion")]  # fmt: skip

    def vector(text):
        t = text.lower()
        return embeddings.normalise([1.0 if any(w in t for w in g) else 0.0 for g in groups]
                                    + [0.05])  # fmt: skip

    monkeypatch.setattr(embeddings, "embed", lambda texts, user=None: [vector(t) for t in texts])
    passages.refresh_all()
    assert not ArticlePassage.objects.filter(embedding__isnull=True).exists()
    hits = passages.search("Cât sodiu e prea mult?")
    ours = [h for h in hits if h.article == article]  # seeded articles are indexed too
    assert ours and ours[0].method == "embedding" and ours[0].passage.heading == "Salt"


# ---------- Through the chat ----------
def test_chat_answer_from_data_with_explanation_and_links(member_client, member, pizzas,
                                                          article, provider):  # fmt: skip
    ctx = f"product:{pizzas['scanned'].pk}"
    question = "Which pizza has less salt than this, and how do I compare the labels?"
    r = member_client.post(reverse("chat-send"), {"context": ctx, "text": question}, **HX)
    assert r.status_code == 200
    answer = ChatMessage.objects.get(user=member, context=ctx, role="ola")
    # Products, values and the Magazine quote all come from the data: no AI call at all.
    assert provider.calls == []
    assert "Low Salt Veggie (Test Pizzeria): 0.8 g" in answer.text
    assert "Pepperoni (Test Pizzeria): 1 g" in answer.text
    assert "From the Magazine, “How to compare two labels”:" in answer.text
    assert "[" not in answer.text and "matches" not in answer.text
    urls = [s["url"] for s in answer.sources]
    assert urls == [
        reverse("product", args=[pizzas["scanned"].pk]),
        reverse("product", args=[pizzas["low"].pk]),
        reverse("product", args=[pizzas["mid"].pk]),
        reverse("learn-article", args=[article.slug]),
    ]
    body = r.content.decode()
    assert 'class="chat-cards"' in body and reverse("learn-article", args=[article.slug]) in body
    sent = json.dumps(provider.calls)
    assert "Mystery" not in sent and "Reine" not in sent and "Four Cheese" not in sent
    for link in urls:  # every link opens
        assert member_client.get(link).status_code == 200


def test_romanian_answer_written_from_data_without_translation(member, pizzas, provider):
    """The bug report's question: written in Romanian from the data (no model, no markers)."""
    out, log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['scanned'].pk}", "ce alta pizza are mai putina sarE?",
        pizzas["scanned"],
    )  # fmt: skip
    assert provider.calls == [] and log is None
    assert out.startswith("Cu mai puțină sare decât acesta (1,2 g la 100 g), din categoria")
    assert "- Low Salt Veggie (Test Pizzeria): 0,8 g" in out and "Regatul Unit" in out
    assert "[" not in out and "matches" not in out
    assert len(sources) == 3


def test_comparison_alone_makes_no_ai_call(member, pizzas, article, provider):
    out, log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['scanned'].pk}", "Which pizza has less salt than this?",
        pizzas["scanned"],
    )  # fmt: skip
    assert provider.calls == [] and log is None
    assert "Low Salt Veggie (Test Pizzeria): 0.8 g" in out and "Four Cheese" not in out
    assert len(sources) == 3 and all(s["kind"] == "product" for s in sources)


def test_zero_results_short_answer_from_data(member, pizzas, provider):
    out, _log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['low'].pk}", "Which pizza has less salt than this?",
        pizzas["low"],
    )  # fmt: skip
    assert provider.calls == []  # English question: nothing to translate, no AI at all
    assert out.startswith("No match in the verified catalogue: no other product in the “Pizzas”")
    assert [(s["title"], s["url"], s["subtitle"]) for s in sources] == [
        ("Low Salt Veggie", reverse("product", args=[pizzas["low"].pk]),
         "Test Pizzeria · 0.8 g salt / 100 g"),
    ]  # fmt: skip


def test_ai_failure_keeps_the_data_answer(member, pizzas, article, provider):
    provider.fail = True
    out, _log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['scanned'].pk}",
        "Which pizza has less salt, and how do I compare the labels?", pizzas["scanned"],
    )  # fmt: skip
    assert "Low Salt Veggie (Test Pizzeria): 0.8 g" in out and "Pepperoni" in out
    assert "From the Magazine" in out and "[A1]" not in out
    assert len([s for s in sources if s["kind"] == "product"]) == 3


def test_ai_off_answers_with_material(member, pizzas, site):
    site.ai_enabled = False
    site.save()
    out, log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['scanned'].pk}", "Which pizza has less salt than this?",
        pizzas["scanned"],
    )  # fmt: skip
    assert log is None and "0.8 g" in out and len(sources) == 3


def test_withdrawn_article_not_used_in_a_new_answer(member, pizzas, article, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    question = "Which pizza has less salt than this, and how do I compare the labels?"
    chat.send(member, ctx, question, pizzas["scanned"])
    first = ChatMessage.objects.filter(user=member, role="ola").order_by("-pk").first()
    assert any(s["kind"] == "article" for s in first.sources)
    article.published = False
    article.save()
    chat.send(member, ctx, question, pizzas["scanned"])
    last = ChatMessage.objects.filter(user=member, role="ola").order_by("-pk").first()
    assert all(s["kind"] == "product" for s in last.sources)
    assert "How to compare two labels" not in last.text


def test_no_access_to_another_users_chat_or_notes(client, member, pizzas, article, provider):
    from django.contrib.auth import get_user_model

    from myola.models import Note

    other = get_user_model().objects.create_user("other", password="a-long-test-password-2")
    Note.objects.create(user=other, text="secret pizza note", product=pizzas["scanned"])
    chat.send(member, f"product:{pizzas['scanned'].pk}", QUESTION, pizzas["scanned"])
    assert all("secret" not in json.dumps(c) for c in provider.calls)
    assert "secret" not in ChatMessage.objects.get(user=member, role="ola").text
    client.force_login(other)
    page = client.get(reverse("chat"), {"context": f"product:{pizzas['scanned'].pk}"})
    assert "Low Salt Veggie" not in page.content.decode()
    assert ChatMessage.objects.filter(user=other).count() == 0


# ---------- Values of the open product, follow-ups, recommendations ----------
def test_value_of_the_open_product_existing_zero_unknown(member, pizzas, off_payload, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    out, _, sources = chat.reply_with_sources(member, ctx, "cata sarE are?", pizzas["scanned"])
    assert out == "Sare: 1,2 g la 100 g (așa cum se vinde)." and provider.calls == []
    assert sources[0]["url"] == reverse("product", args=[pizzas["scanned"].pk])
    zero = pizza(off_payload, "5000000000501", "Salt free pizza", 0)
    out, _, _ = chat.reply_with_sources(member, f"product:{zero.pk}", "How much salt is in it?",
                                        zero)  # fmt: skip
    assert out == "Salt: 0 g per 100 g (as sold)."
    out, _, _ = chat.reply_with_sources(member, f"product:{pizzas['unknown'].pk}",
                                        "Câtă sare are?", pizzas["unknown"])  # fmt: skip
    assert out == "Eticheta acestui produs nu dă valoarea pentru sare."


def test_followup_amount_uses_the_previous_values(member, pizzas, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    chat.send(member, ctx, "Which pizza has less salt than this?", pizzas["scanned"])
    _asked, answer = chat.send(member, ctx, "Dar la 200 g?", pizzas["scanned"])
    assert answer.text.splitlines() == [
        "Sare în 200 g (din valorile la 100 g):",
        "- Scanned Margherita (Test Pizzeria): 2,4 g",
        "- Low Salt Veggie (Test Pizzeria): 1,6 g",
        "- Pepperoni (Test Pizzeria): 2 g",
    ]
    assert len(answer.sources) == 3 and provider.calls == []
    # Per 100 g values asked in ml: a short question back, never a conversion.
    _asked, answer = chat.send(member, ctx, "and for 250 ml?", pizzas["scanned"])
    assert answer.text == "These values are per 100 g. For which amount in g?"


def test_followup_without_anything_before_asks_back(member, pizzas, provider):
    out, _, sources = chat.reply_with_sources(
        member, f"product:{pizzas['low'].pk}", "Dar la 200 g?", pizzas["low"]
    )
    assert out.startswith("Pentru care produs?") and sources == []


def test_comparison_details_kept_for_diagnosis(member, pizzas, provider):
    ctx = f"product:{pizzas['scanned'].pk}"
    chat.send(member, ctx, "ce alta pizza are mai putina sarE?", pizzas["scanned"])
    answer = ChatMessage.objects.filter(user=member, role="ola").order_by("-pk").first()
    d = answer.details
    assert d["kind"] == "comparison" and d["markets"] == [UK] and d["category"] == "en:pizzas"
    assert d["candidates"] == 4 and d["excluded"] == {"no_value": 1, "not_better": 1}
    assert "excluded" not in answer.text and "[" not in answer.text


def test_scanned_product_question_without_a_product(member, pizzas, provider):
    out, _, sources = chat.reply_with_sources(
        member, "personal", "Ce pizza are mai puțină sare decât produsul scanat?"
    )
    assert out == "Deschide sau scanează mai întâi un produs: compar alte produse cu el."
    assert sources == []


@pytest.fixture
def fishes(db, off_payload):
    cache.clear()
    tags = ["en:seafood", "en:fishes", "en:fatty-fishes"]

    def fish(code, name, fat, tags=tags):
        return generic(off_payload, code, name, tags, (UK,), **{"fat_100g": fat})

    return [
        fish("5000000000601", "Smoked mackerel", 25),
        fish("5000000000602", "Salmon fillets", 15.1),
        fish("5000000000603", "Cod fillets", 0.7, tags=["en:seafood", "en:fishes"]),
    ]


def test_recommendation_resolves_these_fats_from_the_conversation(member, fishes, provider):
    """The exact question of the request, after a message about omega-3, in the general chat
    (where a chat started from the diary lands)."""
    provider.text = "Omega-3 are fats found in oily fish."
    chat.send(member, "personal", "Ce sunt grăsimile omega-3?")
    _, answer = chat.send(member, "personal",
                          "Ce pește îmi recomanzi care să aibă aceste grăsimi?")  # fmt: skip
    lines = answer.text.splitlines()
    assert lines[0] == "Despre omega-3, din mesajul tău anterior:"
    assert "nu are valori pentru omega-3" in lines[1]
    assert lines[3:] == [
        "- Smoked mackerel (Brand): 25 g grăsimi totale la 100 g",
        "- Salmon fillets (Brand): 15,1 g grăsimi totale la 100 g",
    ]
    assert [s["url"] for s in answer.sources] == [
        reverse("product", args=[fishes[0].pk]), reverse("product", args=[fishes[1].pk]),
    ]  # fmt: skip
    assert answer.details["subject"] == "omega-3" and answer.details["origin"] == "conversation"


def test_recommendation_by_a_recorded_nutrient(member, fishes, provider):
    out, _, sources = chat.reply_with_sources(
        member, "personal", "Recommend a fish with the least fat"
    )
    assert out.splitlines()[1] == "- Cod fillets (Brand): 0.7 g fat per 100 g"
    assert sources[0]["url"] == reverse("product", args=[fishes[2].pk])

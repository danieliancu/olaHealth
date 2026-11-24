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
from ai.models import AIAnswer
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
    assert c.problem == "" and c.category == "en:pizzas" and c.market == UK
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
    text = rag.deterministic_answer(rag.build_material(c, []))
    assert "salt is not known" in text
    lowest = rag.compare_products(QUESTION, pizzas["low"], member)
    assert lowest.matches == [] and lowest.problem == ""
    assert "None of the" in rag.deterministic_answer(rag.build_material(lowest, []))


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
def test_chat_answer_cites_and_links_only_selected_sources(member_client, member, pizzas,
                                                         article, provider):  # fmt: skip
    provider.text = (
        "Low Salt Veggie has 0.8 g of salt per 100 g [P1] and Pepperoni 1 g [P2], less than "
        "your 1.2 g [P0]. Compare per 100 g, never per serving [A1]. See also [P7] [A9]."
    )
    ctx = f"product:{pizzas['scanned'].pk}"
    r = member_client.post(reverse("chat-send"), {"context": ctx, "text": QUESTION}, **HX)
    assert r.status_code == 200
    answer = ChatMessage.objects.get(user=member, context=ctx, role="ola")
    assert "[P7]" not in answer.text and "[A9]" not in answer.text and "[P1]" in answer.text
    urls = [s["url"] for s in answer.sources]
    assert urls == [
        reverse("product", args=[pizzas["scanned"].pk]),
        reverse("product", args=[pizzas["low"].pk]),
        reverse("product", args=[pizzas["mid"].pk]),
        reverse("learn-article", args=[article.slug]),
    ]
    body = r.content.decode()
    assert 'class="chat-sources"' in body and reverse("learn-article", args=[article.slug]) in body
    # The model got only the selected material: the comparison and the passage.
    facts = provider.facts()
    assert set(facts) == {"comparison", "magazine"}
    assert [m["name"] for m in facts["comparison"]["matches"]] == [
        "Low Salt Veggie (Test Pizzeria)", "Pepperoni (Test Pizzeria)",
    ]  # fmt: skip
    assert facts["comparison"]["matches"][0]["value"] == "0.8 g"
    assert facts["comparison"]["scanned_product"]["value"] == "1.2 g"
    assert facts["magazine"][0]["article"] == article.title
    assert "Mystery" not in json.dumps(facts) and "Reine" not in json.dumps(facts)
    assert "RAG" not in provider.calls[0]["system"] and "matches" in provider.calls[0]["system"]
    for link in urls:  # every link opens
        assert member_client.get(link).status_code == 200


def test_ai_failure_gives_the_same_material_without_ai(member, pizzas, article, provider):
    provider.fail = True
    out, log, sources = chat.reply_with_sources(
        member, f"product:{pizzas['scanned'].pk}", "Which pizza has less salt than this?",
        pizzas["scanned"],
    )  # fmt: skip
    assert log.outcome == AIAnswer.Outcome.FAILED
    assert "Low Salt Veggie (Test Pizzeria): 0.8 g [P1]" in out and "Pepperoni" in out
    assert "Four Cheese" not in out
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
    provider.text = "Compare per 100 g [A1]."
    ctx = f"product:{pizzas['scanned'].pk}"
    chat.send(member, ctx, QUESTION, pizzas["scanned"])
    assert "magazine" in provider.facts()
    article.published = False
    article.save()
    chat.send(member, ctx, QUESTION, pizzas["scanned"])
    assert "magazine" not in provider.facts()
    last = ChatMessage.objects.filter(user=member, role="ola").order_by("-pk").first()
    assert all(s["kind"] == "product" for s in last.sources)


def test_no_access_to_another_users_chat_or_notes(client, member, pizzas, provider):
    from django.contrib.auth import get_user_model

    from myola.models import Note

    provider.text = "Low Salt Veggie [P1]."
    other = get_user_model().objects.create_user("other", password="a-long-test-password-2")
    Note.objects.create(user=other, text="secret pizza note", product=pizzas["scanned"])
    chat.send(member, f"product:{pizzas['scanned'].pk}", QUESTION, pizzas["scanned"])
    assert "secret" not in json.dumps(provider.facts())
    client.force_login(other)
    page = client.get(reverse("chat"), {"context": f"product:{pizzas['scanned'].pk}"})
    assert "Low Salt Veggie [P1]" not in page.content.decode()
    assert ChatMessage.objects.filter(user=other).count() == 0

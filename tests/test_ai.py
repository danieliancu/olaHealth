import sys
import types
from decimal import Decimal

import pytest
from django.utils import timezone

from ai import service
from ai.models import AIUsage
from ai.pricing import cost_usd, price_for
from ai.providers import ProviderError, get_provider
from ai.providers.fake import FakeProvider

FACTS = {"product": {"name": "Greek style yogurt", "brand": "Test Dairy"}, "sugars": "3.6 g"}


@pytest.fixture
def site(db):
    from staff.models import SiteSettings

    s = SiteSettings.load()
    s.ai_enabled = True
    s.ai_provider = "anthropic"
    s.ai_model = "claude-haiku-5-5"
    s.ai_daily_messages_per_user = 3
    s.ai_monthly_budget_gbp = Decimal("15.00")
    s.usd_to_gbp = Decimal("0.75")
    s.save()
    return s


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider("claude-haiku-5-5")
    chosen = []

    def get(name, model=""):
        chosen.append((name, model))
        return provider

    monkeypatch.setattr(service, "get_provider", get)
    provider.chosen = chosen
    return provider


def test_disabled_returns_none(site, fake, member):
    site.ai_enabled = False
    site.save()
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert fake.calls == []


def test_answer_records_usage_with_cost(site, fake, member):
    text = service.phrase(member, FACTS, "Is it sugary?", [])
    assert text.startswith("Based on the facts:") and "Greek style yogurt" in text
    assert fake.chosen == [("anthropic", "claude-haiku-5-5")]
    row = AIUsage.objects.get(user=member, day=timezone.localdate())
    assert row.messages == 1
    assert row.input_tokens > 0 and row.output_tokens > 0
    assert row.cost_usd == cost_usd("claude-haiku-5-5", row.input_tokens, row.output_tokens)
    assert row.cost_usd > 0
    service.phrase(member, FACTS, "And salt?", [])
    row.refresh_from_db()
    assert row.messages == 2


def test_daily_limit(site, fake, member):
    AIUsage.objects.create(user=member, day=timezone.localdate(), messages=3)
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert fake.calls == []


def test_monthly_budget_counts_all_users(site, fake, member, django_user_model):
    other = django_user_model.objects.create_user(username="other", password="x-long-pass-1")
    # $19.99 is £14.99 at 0.75: still under the £15 budget.
    row = AIUsage.objects.create(user=other, day=timezone.localdate(), cost_usd=Decimal("19.99"))
    assert service.phrase(member, FACTS, "Is it sugary?", []) is not None
    row.cost_usd = Decimal("20.00")  # £15.00: the budget is reached
    row.save()
    fake.calls.clear()
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert fake.calls == []


def test_provider_error_returns_none_and_counts_the_message(site, fake, member):
    fake.fail = True
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    # The message slot was reserved before the call (no tokens reported by this error).
    row = AIUsage.objects.get()
    assert row.messages == 1 and row.input_tokens == 0


def test_unconfigured_provider_returns_none(site, member, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert get_provider("anthropic") is None
    assert get_provider("nope") is None
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None


def test_private_keys_stripped_and_history_trimmed(site, fake, member):
    facts = {
        **FACTS,
        "email": "member@example.com",
        "conditions": ["diabetes"],
        "user": {"name": "Member"},
        "nested": [{"Email": "x@example.com", "salt": "0.1 g"}],
    }
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
    service.phrase(member, facts, "Is it sugary?", history)
    sent = fake.calls[0]["messages"]
    blob = str(sent)
    assert "example.com" not in blob and "diabetes" not in blob and "Member" not in blob
    assert "Greek style yogurt" in blob and "0.1 g" in blob  # product facts are kept
    assert len(sent) == service.HISTORY_LIMIT + 1
    assert sent[0]["content"] == "q4" and sent[-1]["content"].endswith("Is it sugary?")
    assert fake.calls[0]["max_tokens"] == service.MAX_TOKENS
    assert "medical advice" in fake.calls[0]["system"]


def test_history_must_start_with_user(site, fake, member):
    history = [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "q"}]
    service.phrase(member, FACTS, "Q?", history)
    assert fake.calls[0]["messages"][0] == {"role": "user", "content": "q"}


def test_pricing():
    assert price_for("claude-haiku-5-5") == (Decimal("0.10"), Decimal("0.50"))
    assert price_for("gpt-5-nano-2025-08-07") == (Decimal("0.05"), Decimal("0.40"))
    assert price_for("mystery") == (Decimal("1.00"), Decimal("5.00"))
    assert cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == Decimal("0.600000")


# --- Anthropic adapter with a fake SDK module -------------------------------------------


class _APIError(Exception):
    pass


def _fake_anthropic(monkeypatch, response=None, error=None):
    calls = {}

    class Messages:
        def create(self, **kwargs):
            calls["create"] = kwargs
            if error is not None:
                raise error
            return response

    class Anthropic:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.messages = Messages()

    module = types.SimpleNamespace(Anthropic=Anthropic, APIError=_APIError)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return calls


def _response(text="Short answer.", stop_reason="end_turn"):
    return types.SimpleNamespace(
        content=[
            types.SimpleNamespace(type="thinking", thinking="..."),
            types.SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        usage=types.SimpleNamespace(input_tokens=120, output_tokens=30),
        model="claude-haiku-5-5",
    )


def test_anthropic_request_shape(monkeypatch):
    calls = _fake_anthropic(monkeypatch, _response())
    provider = get_provider("anthropic", "")
    reply = provider.reply("SYS", [{"role": "user", "content": "hi"}], 400)
    assert reply.text == "Short answer."
    assert (reply.input_tokens, reply.output_tokens, reply.model) == (120, 30, "claude-haiku-5-5")
    sent = calls["create"]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["max_tokens"] == 400 and sent["system"] == "SYS"
    assert sent["output_config"] == {"effort": "low"}
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    assert calls["client"]["max_retries"] == 0 and calls["client"]["timeout"] <= 10


def test_anthropic_refusal_and_api_error(monkeypatch):
    _fake_anthropic(monkeypatch, _response(stop_reason="refusal"))
    with pytest.raises(ProviderError):
        get_provider("anthropic").reply("SYS", [{"role": "user", "content": "hi"}], 400)
    _fake_anthropic(monkeypatch, error=_APIError("boom"))
    with pytest.raises(ProviderError):
        get_provider("anthropic").reply("SYS", [{"role": "user", "content": "hi"}], 400)


def test_refusal_through_service_returns_none_but_is_counted(site, member, monkeypatch):
    """A refusal used tokens: it counts against the daily limit and the monthly budget."""
    _fake_anthropic(monkeypatch, _response(stop_reason="refusal"))
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    row = AIUsage.objects.get()
    assert row.messages == 1 and row.input_tokens > 0 and row.cost_usd > 0


def test_empty_answer_is_retried_once_with_room_and_minimal_thinking(site, fake, member):
    fake.empty = 1  # the first call answers with no text (thinking used the allowance)
    text = service.phrase(member, FACTS, "Is it sugary?", [])
    assert text.startswith("Based on the facts:")
    assert [c["max_tokens"] for c in fake.calls] == [400, 1200]
    assert [c["short_thinking"] for c in fake.calls] == [False, True]
    row = AIUsage.objects.get(user=member)
    assert row.messages == 1  # one question, even with the retry
    assert row.output_tokens >= 400  # the empty call is paid for and counted


def test_still_empty_or_refused_falls_back_without_more_calls(site, fake, member):
    fake.empty = 2
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert len(fake.calls) == 2  # never more than one retry
    fake.calls.clear()
    fake.empty, fake.fail = 0, True  # a refusal/API error is not retried
    assert service.phrase(member, FACTS, "And salt?", []) is None
    assert len(fake.calls) == 1


def test_chat_answer_is_never_blank(member, monkeypatch):
    from myola.services import chat

    monkeypatch.setattr(chat, "_answer", lambda *a, **k: ("  ", None))
    assert chat.answer(member, "personal", "anything") == chat.NO_ANSWER


def test_openai_retry_asks_for_minimal_reasoning(monkeypatch):
    from ai.providers.openai import MINIMAL_EFFORT

    assert MINIMAL_EFFORT.match("gpt-5-nano") and MINIMAL_EFFORT.match("gpt-5-mini-2025-08-07")
    assert not MINIMAL_EFFORT.match("gpt-5.1") and not MINIMAL_EFFORT.match("o4-mini")


def test_richer_answers_send_ingredients_and_additives_only_when_on(
    site, fake, member, off_payload
):
    from catalog.services.importer import import_off_product
    from myola.services import chat

    product = import_off_product(
        off_payload(ingredients_text="Milk, sugar, emulsifier (E471).", additives_tags=["en:e471"])
    )
    site.ai_send_ingredients = False
    site.save()
    chat.answer(member, f"product:{product.pk}", "What is in it?", product)
    sent = fake.calls[-1]["messages"][-1]["content"]
    assert "Milk, sugar" not in sent and "additives" not in sent
    site.ai_send_ingredients = True
    site.save()
    chat.answer(member, f"product:{product.pk}", "What is in it?", product)
    sent = fake.calls[-1]["messages"][-1]["content"]
    assert "Milk, sugar, emulsifier (E471)." in sent
    # Allergy questions stay rule-based: never sent to the provider, ingredients or not.
    n = len(fake.calls)
    chat.answer(member, f"product:{product.pk}", "Is it gluten free?", product)
    assert len(fake.calls) == n


def test_answers_follow_the_question_language_but_allergy_stays_rule_based(site, fake, member):
    from ai.service import SYSTEM_PROMPT
    from myola.services import chat

    assert "language of the user's question" in SYSTEM_PROMPT
    questions = [
        "Are alergeni?",
        "E potrivit pentru intoleranța la lactoză?",
        "¿Tiene alérgenos?",
        "Contient-il des allergènes ?",
        "Ist das glutenfrei?",
        "Czy zawiera orzechy?",
    ]
    for q in questions:
        assert chat.detect_intent(q) == "restrictions", q


def test_general_questions_in_the_general_chat(site, fake, member):
    from ai.service import GENERAL_SYSTEM_PROMPT
    from myola.services import chat

    reply = chat.answer(member, "personal", "Are carbohydrates good for you?")
    call = fake.calls[-1]
    assert call["system"].startswith(GENERAL_SYSTEM_PROMPT) and "NHS 111" in GENERAL_SYSTEM_PROMPT
    assert call["system"].endswith(
        "The question is in British English: write the whole answer in it."
    )
    # Only the question goes out: no facts, no user data.
    assert call["messages"] == [{"role": "user", "content": "Are carbohydrates good for you?"}]
    assert reply.startswith("Based on the facts")
    # Lists, scans and allergies stay rule-based in the general chat too.
    n = len(fake.calls)
    chat.answer(member, "personal", "What did I scan recently?")
    chat.answer(member, "personal", "Are alergeni in lapte?")
    assert len(fake.calls) == n
    # Switched off: the rule-based "I can't answer that yet".
    site.ai_general_questions = False
    site.save()
    assert chat.answer(member, "personal", "Is bread healthy?").startswith(chat.LIMIT_TEXT)
    assert len(fake.calls) == n


def test_no_general_ai_for_people_with_an_eating_disorder(site, fake, member, monkeypatch):
    from myola.models import Profile
    from myola.services import chat

    monkeypatch.setattr(Profile, "eating_disorder", property(lambda self: True))
    chat.answer(member, "personal", "Are carbohydrates good for you?")
    assert fake.calls == []


# ---------- Quality and safety: log, guard, hand-back to the rules, follow-ups ----------


def test_every_provider_call_is_logged_with_cost(site, fake, member):
    from ai.models import AIAnswer

    result = service.ask(member, FACTS, "Is it sugary?", [], context="product:x")
    log = AIAnswer.objects.get()
    assert result.log == log and result.text == log.answer
    assert log.outcome == "answered" and log.kind == "product" and log.context == "product:x"
    assert log.question == "Is it sugary?" and log.cost_usd > 0 and log.input_tokens > 0


def test_no_log_when_no_call_is_made(site, fake, member):
    from ai.models import AIAnswer

    site.ai_enabled = False
    site.save()
    assert service.ask(member, FACTS, "Is it sugary?", []).log is None
    assert not AIAnswer.objects.exists()


def test_failed_calls_are_logged(site, fake, member):
    fake.fail = True
    result = service.ask(member, FACTS, "Is it sugary?", [])
    assert result.text is None and result.log.outcome == "failed"
    assert result.log.reason == "provider error"


@pytest.mark.parametrize(
    "text",
    [
        "Yes, it is safe for people with a nut allergy.",
        "This yogurt is gluten-free.",
        "Este sigur pentru alergici.",
        "Produsul este fără gluten.",
        "You should eat 1,500 kcal a day.",
        "Aim to lose 1 kg a week.",
    ],
)
def test_guard_blocks_unsafe_claims(text):
    assert service.guard(text, general=False)


def test_guard_blocks_doses_only_in_general_answers():
    assert service.guard("Take 1000 mg of vitamin C.", general=True) == "dose"
    assert service.guard("It has 400 mg of calcium per 100 g.", general=False) == ""


@pytest.mark.parametrize(
    "text",
    [
        "It is not safe for people with a milk allergy: it contains milk.",
        "I can't say whether it is gluten-free; check the label.",
        "Adults need around 2,000 kcal a day on average.",
        "Sugars 3.6 g per 100 g, which is low.",
        "Nu pot spune dacă este sigur pentru alergici.",
    ],
)
def test_guard_lets_safe_answers_through(text):
    assert service.guard(text, general=False) == ""


def test_blocked_answer_falls_back_to_the_rules(site, fake, member, monkeypatch):
    from ai.models import AIAnswer
    from ai.providers.base import Reply

    monkeypatch.setattr(
        fake, "reply", lambda *a, **k: Reply("It is safe for your allergy.", 10, 10, "fake-1")
    )
    result = service.ask(member, FACTS, "What is in it?", [])
    assert result.text is None
    log = AIAnswer.objects.get()
    assert log.outcome == "blocked" and log.reason == "allergy safety claim"
    assert log.answer == "It is safe for your allergy."


def test_model_can_hand_a_question_back_to_the_rules(site, fake, member, monkeypatch):
    from ai.providers.base import Reply
    from ai.service import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT
    from myola.services import chat

    assert "ROUTE:restrictions" in SYSTEM_PROMPT and "ROUTE:scans" in GENERAL_SYSTEM_PROMPT
    monkeypatch.setattr(fake, "reply", lambda *a, **k: Reply("ROUTE:scans", 10, 2, "fake-1"))
    out, log = chat.reply(member, "personal", "Ce produse am verificat ieri, te rog?")
    # The rule answer, in English with a Romanian note (scan history is never sent to the AI).
    assert out.endswith(chat.rule_answer("scans", chat.build_facts(member, "personal")))
    assert out.startswith("Acest răspuns conține notițele, listele sau istoricul tău")
    assert log.outcome == "routed" and log.reason == "scans"


def test_follow_up_questions_get_the_last_ai_answers(site, fake, member):
    from myola.services import chat

    chat.send(member, "personal", "Is bread healthy?")
    chat.send(member, "personal", "And rice?")
    sent = fake.calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "Is bread healthy?" and sent[-1]["content"] == "And rice?"
    # Another chat starts fresh.
    chat.send(member, "product:other", "Hello there")
    assert len(fake.calls[-1]["messages"]) == 1


def test_only_ai_answers_can_be_rated(site, fake, member, member_client):
    from myola.services import chat

    _, ai_msg = chat.send(member, "personal", "Is bread healthy?")
    _, rule_msg = chat.send(member, "personal", "What did I scan recently?")
    assert ai_msg.ai_answer_id and rule_msg.ai_answer_id is None
    from django.urls import reverse

    url = reverse("chat-rate", args=[ai_msg.ai_answer_id])
    r = member_client.post(url, {"value": "up"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and 'aria-pressed="true"' in r.content.decode()
    ai_msg.ai_answer.refresh_from_db()
    assert ai_msg.ai_answer.rating == 1
    member_client.post(url, {"value": "up"}, HTTP_HX_REQUEST="true")  # pressed again: cleared
    ai_msg.ai_answer.refresh_from_db()
    assert ai_msg.ai_answer.rating is None
    page = member_client.get(reverse("chat") + "?context=personal").content.decode()
    assert page.count('class="chat-rate"') == 1


def test_nobody_rates_someone_elses_answer(site, fake, member, client, django_user_model):
    from django.urls import reverse

    from myola.services import chat

    _, msg = chat.send(member, "personal", "Is bread healthy?")
    other = django_user_model.objects.create_user(username="o", password="x-long-pass-1")
    client.force_login(other)
    r = client.post(reverse("chat-rate", args=[msg.ai_answer_id]), {"value": "down"})
    assert r.status_code == 404


def test_staff_ai_answers_page_hides_who_asked(site, fake, member, client, django_user_model):
    from django.urls import reverse

    from myola.services import chat

    chat.send(member, "personal", "Is bread healthy?")
    admin = django_user_model.objects.create_superuser(
        username="boss", email="boss@example.com", password="x-long-pass-1"
    )
    client.force_login(admin)
    html = client.get(reverse("staff:ai-answers")).content.decode()
    assert "Is bread healthy?" in html and "Answered by the AI" in html
    assert member.email not in html and member.username not in html
    filtered = client.get(reverse("staff:ai-answers") + "?outcome=blocked").content.decode()
    assert "Is bread healthy?" not in filtered


def test_old_ai_answers_are_deleted(site, fake, member):
    from datetime import timedelta

    from ai.models import AIAnswer
    from ai.tasks import purge_old_ai_answers

    service.ask(member, FACTS, "Is it sugary?", [])
    service.ask(member, FACTS, "And salt?", [])
    old = AIAnswer.objects.first()
    AIAnswer.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
    assert purge_old_ai_answers() == 1 and AIAnswer.objects.count() == 1


def test_one_language_only_in_the_general_information_line():
    from ai.language import strip_english, with_disclaimer

    seen = (
        "Ouăle pot face parte dintr-o dietă echilibrată. Acesta este un ghid general, nu un "
        "sfat personal. this is general information, not personal advice."
    )
    out = with_disclaimer(seen, "ro")
    assert "general information" not in out and out.count("ghid general") == 1
    assert "Acestea sunt informații generale" not in out  # no second Romanian line
    # Question language unknown: the answer's own language decides.
    assert "general information" not in with_disclaimer(seen, "")
    own_line = (
        "Da, sunt buni pentru sănătate.\n\n- This is general information, not personal advice."
    )
    out = with_disclaimer(own_line, "ro")
    assert out.startswith("Da, sunt buni") and "general information" not in out
    assert out.endswith("Acestea sunt informații generale, nu sfaturi personalizate.")
    english = "Eggs are fine for most people. This is general information, not personal advice."
    assert with_disclaimer(english, "en") == english and strip_english(english) == english
    assert (
        strip_english(
            "Are 5,1 g de proteine la 100 g. This is general information, not personal advice."
        )
        == "Are 5,1 g de proteine la 100 g."
    )


# ---------- Rule answers in the language of the question ----------
def _translating(monkeypatch, fake, text="Răspuns tradus."):
    from ai.providers.base import Reply

    monkeypatch.setattr(fake, "reply", lambda system, *a, **k: Reply(
        text if system.startswith("You translate") else "ROUTE:restrictions", 10, 5, "fake-1"
    ))  # fmt: skip


def test_rule_answers_are_translated_with_consent(site, fake, member, monkeypatch):
    from ai.models import AIAnswer
    from myola.services import chat
    from myola.services.profiles import get_profile

    _translating(monkeypatch, fake)
    question = "Ce alergii am setate în profil?"
    out, _log = chat.reply(member, "personal", question)
    assert out.startswith("Acest răspuns conține datele tale") and "Răspuns tradus" not in out
    profile = get_profile(member)
    profile.ai_use_preferences = True
    profile.save()
    out, _log = chat.reply(member, "personal", question)
    assert out == "Răspuns tradus."
    t = AIAnswer.objects.filter(user=member, kind="translation").latest("created_at")
    assert t.outcome == "answered" and t.answer == ""  # own data: the text is not kept


def test_english_questions_are_never_translated(site, fake, member, monkeypatch):
    from myola.services import chat

    _translating(monkeypatch, fake)
    out, _log = chat.reply(member, "personal", "What allergies do I have set?")
    assert "Răspuns tradus" not in out and "Acest" not in out


def test_english_with_a_note_only_when_the_ai_cannot_help(site, fake, member):
    from myola.services import chat
    from myola.services.profiles import get_profile

    profile = get_profile(member)
    profile.ai_use_preferences = True
    profile.save()
    site.ai_enabled = False
    site.save()
    out, _log = chat.reply(member, "personal", "Ce alergii am setate în profil?")
    assert out.startswith("Momentan nu pot traduce acest răspuns")
    assert out.endswith(chat.rule_answer("restrictions", chat.build_facts(member, "personal")))


def test_translation_that_adds_text_is_refused(site, fake, member, monkeypatch):
    from ai.service import translate

    _translating(monkeypatch, fake, text="x" * 500)
    out, log = translate(member, "Short.", "ro")
    assert out is None and log.outcome == "failed" and log.reason == "too long"


def test_public_rule_answers_are_translated_without_consent(site, fake, member, monkeypatch):
    from myola.services import chat

    _translating(monkeypatch, fake)
    assert chat._personal_data("values", {"notes": []}) == "public"
    assert chat._personal_data("values", {"notes": ["mine"]}) == "private"
    assert chat._personal_data("restrictions", {"notes": []}) == "diets"
    for intent in ("remember", "avoid", "scans"):
        assert chat._personal_data(intent, {"notes": []}) == "private"
    profile = chat.get_profile(member)
    out, _log = chat._localise(
        member, profile, "Sugars 3 g.", None, "ro", "Câte zaharuri are?", "", kind="public"
    )
    assert out == "Răspuns tradus."


def test_private_answers_translated_only_with_my_data_switch(site, fake, member, monkeypatch):
    from myola.services import chat

    _translating(monkeypatch, fake)
    profile = chat.get_profile(member)
    args = (member, profile, "You asked me to remember: x.", None, "ro", "Ce știi despre mine?", "")
    out, _log = chat._localise(*args, kind="private")
    assert out.startswith("Acest răspuns conține notițele") and "Let Ask Foodo use my" in out
    profile.ai_use_my_data = True
    profile.save()
    out, _log = chat._localise(*args, kind="private")
    assert out == "Răspuns tradus."
    from ai.models import AIAnswer

    t = AIAnswer.objects.filter(user=member, kind="translation").latest("created_at")
    assert t.answer == ""  # own data: the text is not kept

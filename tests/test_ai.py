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
    s.ai_monthly_budget_usd = Decimal("20.00")
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
    AIUsage.objects.create(user=other, day=timezone.localdate(), cost_usd=Decimal("20.00"))
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert fake.calls == []


def test_provider_error_returns_none_and_records_nothing(site, fake, member):
    fake.fail = True
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert not AIUsage.objects.exists()


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
    assert calls["client"]["max_retries"] == 1 and calls["client"]["timeout"] <= 30


def test_anthropic_refusal_and_api_error(monkeypatch):
    _fake_anthropic(monkeypatch, _response(stop_reason="refusal"))
    with pytest.raises(ProviderError):
        get_provider("anthropic").reply("SYS", [{"role": "user", "content": "hi"}], 400)
    _fake_anthropic(monkeypatch, error=_APIError("boom"))
    with pytest.raises(ProviderError):
        get_provider("anthropic").reply("SYS", [{"role": "user", "content": "hi"}], 400)


def test_refusal_through_service_returns_none(site, member, monkeypatch):
    _fake_anthropic(monkeypatch, _response(stop_reason="refusal"))
    assert service.phrase(member, FACTS, "Is it sugary?", []) is None
    assert not AIUsage.objects.exists()

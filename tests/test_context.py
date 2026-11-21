"""Ask Foodo personal context (myola/context): what the AI may read about a user, with consent."""

from __future__ import annotations

import json

import pytest

from ai import service
from ai.models import AIAnswer
from myola.context import get_source
from myola.context.django_source import DjangoContextSource, sections_for, wants_products
from myola.models import Note, WaterEntry
from myola.services import chat
from myola.services.profiles import get_profile, local_today
from tests.test_ai import fake, site  # noqa: F401  (fixtures)
from tests.test_today import make_entry


def test_sections_follow_the_question():
    assert "diary" in sections_for("What did I eat yesterday?")
    assert "water" in sections_for("Câtă apă am băut săptămâna asta?")
    assert "weight" in sections_for("How is my weight going?")
    assert sections_for("Hello there") == set()
    assert wants_products("Can you recommend a better snack?")
    assert wants_products("Ce să cumpăr în loc de chipsuri?")
    assert chat.is_personal("What did I eat yesterday?", None)
    assert not chat.is_personal("Is sugar bad for you?", None)


@pytest.mark.django_db
def test_user_context_has_their_data_and_never_identity_or_health(member):
    profile = get_profile(member)
    profile.conditions, profile.conditions_note = ["diabetes"], "type 2"
    profile.use_notes, profile.target_kcal = True, 2000
    profile.save()
    today = local_today(profile)
    make_entry(member, day=today, meal="breakfast", name="Porridge")
    WaterEntry.objects.create(user=member, date=today, ml=500)
    Note.objects.create(user=member, kind="general", text="I like oat milk")
    ctx = DjangoContextSource().user_context(
        member, "What did I eat and drink today? Any notes?", today=today
    )
    text = json.dumps(ctx, default=str)
    assert "Porridge" in text and '"water_ml": 500' in text and "oat milk" in text
    assert ctx["targets"]["kcal"] == 2000
    for secret in (member.email, "diabetes", "type 2", member.username):
        assert secret not in text
    assert len(text) <= 6500


@pytest.mark.django_db
def test_eating_disorder_answers_never_get_calories_targets_or_weight(member):
    profile = get_profile(member)
    profile.conditions, profile.track_weight, profile.target_kcal = ["eating_disorder"], True, 1800
    profile.save()
    make_entry(member, meal="lunch")
    ctx = DjangoContextSource().user_context(
        member, "What did I eat today and my weight?", today=local_today(profile)
    )
    text = json.dumps(ctx, default=str)
    assert ctx.get("sensitive") is True
    assert "kcal" not in text and "weights" not in ctx and "1800" not in text


def test_local_database_source_falls_back_until_connected(settings):
    settings.AI_CONTEXT_SOURCE = "local_db"
    assert get_source().name == "django"


def test_guard_lets_users_hear_their_own_targets_only():
    text = "Your target is 2000 kcal a day."
    assert service.guard(text, general=False, own_targets=frozenset({2000})) == ""
    assert service.guard(text, general=False) == "calorie or weight target"
    assert service.guard("You should eat 1500 kcal a day.", general=False,
                         own_targets=frozenset({2000})) == "calorie or weight target"  # fmt: skip


@pytest.mark.django_db
def test_personal_questions_use_their_data_only_with_consent(site, fake, member):  # noqa: F811
    make_entry(member, meal="breakfast", name="Porridge")
    question = "What did I eat today?"
    chat.reply(member, "personal", question)
    assert not any("asks about themselves" in c["system"] for c in fake.calls)
    profile = get_profile(member)
    profile.ai_use_my_data = True
    profile.save()
    _out, log = chat.reply(member, "personal", question)
    call = fake.calls[-1]
    assert "asks about themselves" in call["system"]
    assert (
        '"me"' in call["messages"][-1]["content"] and "Porridge" in call["messages"][-1]["content"]
    )
    assert log.kind == AIAnswer.Kind.PERSONAL and log.answer == ""  # not kept


@pytest.mark.django_db
def test_allergy_checks_on_a_product_stay_with_the_rules(site, fake, member, off_payload):  # noqa: F811
    from catalog.services.importer import import_off_product

    product = import_off_product(off_payload())
    profile = get_profile(member)
    profile.ai_use_my_data = True
    profile.save()
    chat.reply(member, f"product:{product.pk}", "Is it safe for my allergies?", product)
    assert not any("asks about themselves" in c["system"] for c in fake.calls)

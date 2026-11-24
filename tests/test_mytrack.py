"""My Track (server-rendered): tabs, notes, memories, saved lists, data, Ask Foodo."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from catalog.services.importer import import_off_product
from myola.models import LIMITS, ChatMessage, Memory, Note, ProductMark, ProductVisit
from myola.services import chat, memories
from myola.services.profiles import get_profile

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def other(db):
    return get_user_model().objects.create_user(
        username="other", email="other@example.com", password="another-long-password-2"
    )


@pytest.fixture
def yogurt(off_payload):
    return import_off_product(off_payload())


def tab(client, name, **params):
    return client.get(reverse("ask-foodo"), {"tab": name, **params}).content.decode()


# ---------- Pages ----------
@pytest.mark.django_db
def test_login_required(client):
    for name in ("ask-foodo", "chat", "notes-search"):
        r = client.get(reverse(name))
        assert r.status_code == 302 and "/accounts/login/" in r["Location"]
    for name in ("notes-create", "memory-add", "chat-send", "chat-clear", "scans-clear"):
        r = client.post(reverse(name))
        assert r.status_code == 302 and "/accounts/login/" in r["Location"]


@pytest.mark.django_db
def test_tabs_render_with_aria_current(member_client):
    for name in ("overview", "profile", "notes", "saved", "account"):
        html = tab(member_client, name)
        assert f'href="/ask-foodo/?tab={name}"' in html
        current = re.findall(r'<a href="/ask-foodo/\?tab=(\w+)"[^>]*aria-current="page"', html)
        assert current == [name]
        body = html.split("</head>")[1]
        assert "<script>" not in body and 'style="' not in body and "hx-on" not in body
    assert "Your lists" in tab(member_client, "bogus")  # falls back to the overview


@pytest.mark.django_db
def test_overview_counts_and_recent_scans(member_client, member, yogurt):
    ProductVisit.objects.create(user=member, product=yogurt, code="123")
    ProductMark.objects.create(user=member, product=yogurt, kind="favorite")
    Note.objects.create(user=member, text="Nice")
    html = tab(member_client, "overview")
    assert yogurt.name in html and "Scanned" in html
    assert "You scanned 1 product in the last 7 days (1 recorded in total)." in html
    assert re.search(r"<span>Favourites</span><strong>1</strong>", html)
    assert re.search(r"<span>Notes</span><strong>1</strong>", html)
    # Ask Foodo: no button beside the title any more (tab bar + the overview button).
    assert 'id="ask-foodo"' not in html and "context=personal" in html
    assert "this browser" not in html and "this device" not in html


@pytest.mark.django_db
def test_profile_tab_summary_and_consent(member_client, member):
    profile = get_profile(member)
    profile.goal = "lose_weight"
    profile.restrictions = ["vegan"]
    profile.conditions = ["diabetes"]
    profile.save()
    html = tab(member_client, "profile")
    assert "Losing weight" in html and "Veganism" in html
    assert "Diabetes" not in html  # no consent: never shown
    assert 'href="/start/"' in html and "Go through the questions again" in html
    assert "stored in your account" in html
    profile.health_consent_at = timezone.now()
    profile.save()
    assert "Diabetes" in tab(member_client, "profile")


@pytest.mark.django_db
def test_account_tab_keeps_the_account_actions(member_client):
    html = tab(member_client, "account")
    assert "member@example.com" in html and reverse("account_logout") in html
    assert reverse("account_change_password") in html and reverse("delete-account") in html


# ---------- Notes ----------
@pytest.mark.django_db
def test_notes_crud(member_client, member, yogurt):
    r = member_client.post(
        reverse("notes-create"), {"text": "Too sweet", "kind": "taste", "product": yogurt.pk}
    )
    assert r.status_code == 302
    note = Note.objects.get(user=member)
    assert note.kind == "taste" and note.product == yogurt
    html = tab(member_client, "notes")
    assert "Too sweet" in html and f'id="note-{note.pk}"' in html

    # Inline edit (htmx), then save
    body = member_client.get(reverse("note-edit", args=[note.pk]), **HX).content.decode()
    assert "<textarea" in body and "Too sweet" in body and "<html" not in body
    r = member_client.post(
        reverse("note-edit", args=[note.pk]), {"text": "Quite sweet", "kind": "avoid"}, **HX
    )
    body = r.content.decode()
    assert 'class="note-item kind-avoid"' in body and "Quite sweet" in body
    assert 'hx-swap-oob="true"' in body and "Note updated." in body
    note.refresh_from_db()
    assert note.text == "Quite sweet" and note.product is None

    # Without htmx the edit form is shown in the page
    html = tab(member_client, "notes", edit=note.pk)
    assert f'action="/ask-foodo/notes/{note.pk}/edit/"' in html

    # Empty text is refused
    r = member_client.post(reverse("notes-create"), {"text": " ", "kind": "general"}, **HX)
    assert Note.objects.count() == 1 and "field-error" in r.content.decode()

    r = member_client.post(reverse("note-delete", args=[note.pk]), **HX)
    assert not Note.objects.exists() and "No notes yet" in r.content.decode()


@pytest.mark.django_db
def test_note_from_the_product_page_goes_back(member_client, member, yogurt):
    url = reverse("product", args=[yogurt.pk])
    r = member_client.post(
        reverse("notes-create"),
        {"text": "Buy for Sunday", "kind": "general", "product": yogurt.pk, "next": url},
    )
    assert r.status_code == 302 and r["Location"] == url
    assert Note.objects.get(user=member).product == yogurt
    r = member_client.post(
        reverse("notes-create"), {"text": "x", "kind": "taste", "next": "https://evil.test/"}
    )
    assert r["Location"] == "/ask-foodo/?tab=notes"


@pytest.mark.django_db
def test_notes_search(member_client, member, yogurt):
    Note.objects.create(user=member, text="Great with granola")
    Note.objects.create(user=member, text="Something else", product=yogurt)
    body = member_client.get(reverse("notes-search"), {"q": "granola"}, **HX).content.decode()
    assert "Great with granola" in body and "Something else" not in body
    body = member_client.get(reverse("notes-search"), {"q": yogurt.name[:5]}, **HX).content
    assert b"Something else" in body
    body = member_client.get(reverse("notes-search"), {"q": "zzz"}, **HX).content.decode()
    assert "No note matches your search." in body
    r = member_client.get(reverse("notes-search"), {"q": "granola"})
    assert r.status_code == 302 and "tab=notes" in r["Location"] and "q=granola" in r["Location"]
    html = tab(member_client, "notes", q="granola")
    assert 'hx-trigger="input changed delay:300ms, search"' in html
    assert "Great with granola" in html and "Something else" not in html


@pytest.mark.django_db
def test_notes_are_private(member_client, other):
    note = Note.objects.create(user=other, text="Secret")
    assert "Secret" not in tab(member_client, "notes")
    assert member_client.get(reverse("note-edit", args=[note.pk]), **HX).status_code == 404
    assert member_client.post(reverse("note-delete", args=[note.pk])).status_code == 404
    r = member_client.post(reverse("note-edit", args=[note.pk]), {"text": "x", "kind": "general"})
    assert r.status_code == 404
    assert Note.objects.get(pk=note.pk).text == "Secret"


# ---------- Memories ----------
@pytest.mark.django_db
def test_memories_add_pause_edit_delete(member_client, member):
    r = member_client.post(reverse("memory-add"), {"text": "I prefer plain yogurts"}, **HX)
    body = r.content.decode()
    assert 'id="memories"' in body and "I prefer plain yogurts" in body
    m = Memory.objects.get(user=member)
    assert m.active and m.confirmed_at and m.source == "manual"
    member_client.post(reverse("memory-toggle", args=[m.pk]), **HX)
    m.refresh_from_db()
    assert not m.active
    assert list(memories.used_in_replies(member)) == []
    member_client.post(reverse("memory-toggle", args=[m.pk]))
    m.refresh_from_db()
    assert m.active
    member_client.post(reverse("memory-edit", args=[m.pk]), {"text": "Plain yogurts only"})
    m.refresh_from_db()
    assert m.text == "Plain yogurts only"
    member_client.post(reverse("memory-delete", args=[m.pk]))
    assert not Memory.objects.exists()


@pytest.mark.django_db
def test_memories_are_private(member_client, other):
    m = Memory.objects.create(user=other, text="Theirs", confirmed_at=timezone.now())
    assert member_client.post(reverse("memory-delete", args=[m.pk])).status_code == 404
    assert member_client.post(reverse("memory-toggle", args=[m.pk])).status_code == 404
    assert "Theirs" not in tab(member_client, "notes")


@pytest.mark.django_db
def test_health_information_never_becomes_a_memory(member_client, member):
    for text in ("I take metformin 500 mg", "My diagnosis is diabetes", "Symptoms after milk"):
        r = member_client.post(reverse("memory-add"), {"text": text}, **HX)
        assert "doesn&#x27;t keep diagnoses" in r.content.decode()
    assert not Memory.objects.exists()
    with pytest.raises(memories.MemoryRefused):
        memories.add(member, "insulin at night")


@pytest.mark.django_db
def test_memory_suggestions_need_confirmation(member_client, member, yogurt):
    profile = get_profile(member)
    profile.goal = "eat_healthier"
    profile.restrictions = ["vegan"]
    profile.save()
    ProductMark.objects.create(user=member, product=yogurt, kind="avoid")
    keys = [k for k, _ in memories.suggestions(member)]
    assert keys == ["goal:eat_healthier", "diet:vegan", f"avoid:{yogurt.pk}"]
    assert "Not used until you confirm it." in tab(member_client, "notes")
    assert not Memory.objects.exists()
    member_client.post(reverse("memory-confirm"), {"key": "diet:vegan"}, **HX)
    m = Memory.objects.get(user=member)
    assert m.source == "suggestion" and m.confirmed_at and m.text == "I choose vegan products."
    assert "diet:vegan" not in [k for k, _ in memories.suggestions(member)]
    member_client.post(reverse("memory-confirm"), {"key": "made:up"})
    assert Memory.objects.count() == 1


@pytest.mark.django_db
def test_notes_in_replies_setting(member_client, member):
    member_client.post(reverse("notes-in-replies"), {"use_notes": "on"}, **HX)
    assert get_profile(member).use_notes
    member_client.post(reverse("notes-in-replies"), {})
    assert not get_profile(member).use_notes


# ---------- Saved and data ----------
@pytest.mark.django_db
def test_saved_lists_remove(member_client, member, yogurt):
    ProductMark.objects.create(user=member, product=yogurt, kind="buy_often")
    ProductVisit.objects.create(user=member, product=yogurt, code="123")
    ProductVisit.objects.create(user=member, product=yogurt, code="")
    html = tab(member_client, "saved")
    assert 'id="saved-buy_often"' in html and 'id="saved-avoid"' in html
    assert 'id="saved-scan"' in html and 'href="/saved/"' in html
    r = member_client.post(
        reverse("saved-remove"), {"kind": "buy_often", "product": yogurt.pk}, **HX
    )
    assert "Nothing marked “Buy often”." in r.content.decode()
    assert not ProductMark.objects.exists()
    r = member_client.post(reverse("saved-remove"), {"kind": "scan", "product": yogurt.pk})
    assert r.status_code == 302
    assert list(ProductVisit.objects.values_list("code", flat=True)) == [""]  # views stay
    r = member_client.post(reverse("saved-remove"), {"kind": "x", "product": yogurt.pk})
    assert r.status_code == 404


@pytest.mark.django_db
def test_clear_scans_and_chats(member_client, member, other, yogurt):
    ProductVisit.objects.create(user=member, product=yogurt, code="123")
    ProductVisit.objects.create(user=other, product=yogurt, code="123")
    ChatMessage.objects.create(user=member, role="user", text="hi")
    ChatMessage.objects.create(user=other, role="user", text="hi")
    r = member_client.post(reverse("scans-clear"), {"tab": "profile"})
    assert r.status_code == 302 and r["Location"] == "/ask-foodo/?tab=profile"
    r = member_client.post(reverse("chat-clear"), **HX)
    assert r.status_code == 204 and r["HX-Redirect"] == "/ask-foodo/?tab=profile"
    assert ProductVisit.objects.filter(user=member).count() == 0
    assert ChatMessage.objects.filter(user=member).count() == 0
    assert ProductVisit.objects.filter(user=other).count() == 1
    assert ChatMessage.objects.filter(user=other).count() == 1


# ---------- Ask Foodo ----------
def test_detect_intent():
    cases = {
        "What are the key values?": "values",
        "How much sugar?": "values",
        "What do Nutri-Score and processing mean?": "indicators",
        "Does it fit my restrictions?": "restrictions",
        "Is it vegan?": "restrictions",
        "What do you remember?": "remember",
        "What did I mark as Avoid?": "avoid",
        "What did I scan recently?": "scans",
        "What are my preferences?": "prefs",
        "Tell me a joke": None,
    }
    for text, intent in cases.items():
        assert chat.detect_intent(text) == intent, text


@pytest.mark.django_db
def test_product_intents_use_the_product_facts(member, yogurt):
    ctx = chat.context_for(yogurt)
    values = chat.answer(member, ctx, "key values?", yogurt)
    assert values.startswith("Per 100") and "Energy" in values
    assert "Nutri-Score" in chat.answer(member, ctx, "nutri-score?", yogurt)
    assert "haven't chosen any diet" in chat.answer(member, ctx, "does it fit?", yogurt)
    profile = get_profile(member)
    profile.restrictions = ["vegan"]
    profile.save()
    reply = chat.answer(member, ctx, "does it fit?", yogurt)
    assert reply.startswith("Vegan: ") and "always check the physical label" in reply
    # Without a product, product intents explain where to ask.
    assert chat.answer(member, "personal", "key values?").startswith("Open a product")


@pytest.mark.django_db
def test_personal_intents_use_account_data(member, yogurt):
    assert chat.answer(member, "personal", "avoid?") == "You haven't marked any product as Avoid."
    ProductMark.objects.create(user=member, product=yogurt, kind="avoid")
    assert yogurt.name in chat.answer(member, "personal", "What did I mark as avoid?")
    assert chat.answer(member, "personal", "scans?") == "No scans recorded yet."
    ProductVisit.objects.create(user=member, product=yogurt, code="1")
    reply = chat.answer(member, "personal", "recent scans?")
    assert yogurt.name in reply and "A scan is not a purchase." in reply
    assert chat.answer(member, "personal", "preferences?") == "You haven't set any preferences yet."
    profile = get_profile(member)
    profile.goal = "lose_weight"
    profile.restrictions = ["nuts"]
    profile.save()
    reply = chat.answer(member, "personal", "my preferences?")
    assert "Losing weight" in reply and "allergy: Peanuts" in reply
    reply = chat.answer(member, "personal", "what is the weather")
    assert reply.startswith("I can't answer that yet") and "“What do you remember?”" in reply


@pytest.mark.django_db
def test_only_confirmed_active_memories_are_used(member):
    Memory.objects.create(user=member, text="Unconfirmed one")
    Memory.objects.create(user=member, text="Paused one", active=False, confirmed_at=timezone.now())
    Memory.objects.create(user=member, text="Plain yogurts", confirmed_at=timezone.now())
    reply = chat.answer(member, "personal", "What do you remember?")
    assert "“Plain yogurts”" in reply
    assert "Unconfirmed" not in reply and "Paused" not in reply


@pytest.mark.django_db
def test_notes_are_used_only_when_allowed(member, yogurt):
    Note.objects.create(user=member, text="Granola goes well", product=yogurt)
    ctx = chat.context_for(yogurt)
    assert "From your notes" not in chat.answer(member, ctx, "key values?", yogurt)
    profile = get_profile(member)
    profile.use_notes = True
    profile.save()
    assert "“Granola goes well”" in chat.answer(member, ctx, "key values?", yogurt)
    # Personal context: only notes with matching words.
    assert "From your notes" not in chat.answer(member, "personal", "avoid?")
    assert "Granola" in chat.answer(member, "personal", "avoid granola?")


@pytest.mark.django_db
def test_chat_send_persists_and_trims(member_client, member, yogurt):
    ctx = f"product:{yogurt.pk}"
    r = member_client.post(reverse("chat-send"), {"context": ctx, "intent": "values"}, **HX)
    body = r.content.decode()
    assert body.count('class="chat-msg') == 2 and "What are the key values?" in body
    assert 'hx-swap-oob="true"' in body
    msgs = list(ChatMessage.objects.filter(user=member, context=ctx))
    assert [m.role for m in msgs] == ["user", "ola"] and "Energy" in msgs[1].text
    for i in range(LIMITS["chat_messages"]):
        chat.send(member, "personal", f"question {i}")
    personal = ChatMessage.objects.filter(user=member, context="personal")
    assert personal.count() == LIMITS["chat_messages"]
    assert not personal.filter(text="question 0").exists()
    assert ChatMessage.objects.filter(context=ctx).count() == 2  # other contexts untouched
    # Without htmx: back to the conversation page, which lists the messages.
    r = member_client.post(reverse("chat-send"), {"context": "personal", "text": "scans?"})
    assert r.status_code == 302
    page = member_client.get(reverse("chat"), {"context": "personal"}).content.decode()
    assert "No scans recorded yet." in page


@pytest.mark.django_db
def test_chat_rejects_unknown_contexts(member_client):
    r = member_client.post(reverse("chat-send"), {"context": "admin", "text": "hi"})
    assert r.status_code == 404
    assert member_client.get(reverse("chat"), {"context": "product:nope"}).status_code == 404
    r = member_client.post(reverse("chat-send"), {"context": "personal", "text": ""}, **HX)
    assert r.status_code == 204 and not ChatMessage.objects.exists()


@pytest.mark.django_db
def test_chat_partial_and_product_page(member_client, yogurt):
    r = member_client.get(reverse("chat"), {"context": f"product:{yogurt.pk}"}, **HX)
    body = r.content.decode()
    assert "<html" not in body and f'id="chat-log-product-{yogurt.pk}"' in body
    assert 'hx-post="/ask-foodo/chat/send/"' in body and 'hx-swap="beforeend scroll:bottom"' in body
    assert "Does it fit my restrictions?" not in body  # no suggestion pills
    html = member_client.get(reverse("product", args=[yogurt.pk])).content.decode()
    assert f"context=product%3A{yogurt.pk}" not in html  # no chat button on product pages


def test_account_icon_reopens_the_last_tab(member_client):
    html = member_client.get(reverse("ask-foodo")).content.decode()
    assert 'href="/ask-foodo/?tab=overview"' in html and "Your stats" in html  # default: Overview
    member_client.get(reverse("ask-foodo") + "?tab=notes")
    html = member_client.get(reverse("ask-foodo")).content.decode()
    assert re.search(r'href="/ask-foodo/\?tab=notes"[^>]*aria-current="page"', html)


# ---------- Ask Foodo: product search (catalogue + Open Food Facts) ----------
def test_chat_finds_products_in_our_catalogue(member_client, yogurt):
    from myola.services import chat_products

    reply = chat_products.answer("find greek yogurt")
    assert yogurt.name in reply and f"/p/{yogurt.pk}/" in reply
    assert chat_products.answer("what do you remember?") == ""


def test_chat_searches_open_food_facts_when_needed(member, monkeypatch):
    from myola.services import chat_products
    from off.client import OFFClient

    rows = [{"code": "3000000000012", "product_name": "Oat drink", "brands": "Oaty"}]
    monkeypatch.setattr(OFFClient, "search_text", lambda self, q, page_size=3: rows)
    reply = chat_products.answer("search for oat drink")
    assert "Open Food Facts" in reply and "/lookup/?code=3000000000012" in reply


def test_chat_barcode_and_clickable_links(member_client, member, yogurt):
    code = yogurt.off_code
    from myola.services import chat

    _asked, answered = chat.send(member, chat.PERSONAL, f"what is {code}?")
    assert f"/p/{yogurt.pk}/" in answered.text
    html = member_client.get(reverse("chat")).content.decode()
    assert f'<a href="/p/{yogurt.pk}/">' in html


def test_chat_remembers_where_you_came_from(member_client, yogurt):
    url = reverse("chat")
    html = member_client.get(url, HTTP_REFERER=f"http://testserver/p/{yogurt.pk}/").content.decode()
    assert f'href="/p/{yogurt.pk}/"' in html and f"Back to {yogurt.name}" in html
    # still there after sending a message (no referer on the next visit)
    html = member_client.get(url).content.decode()
    assert f"Back to {yogurt.name}" in html
    html = member_client.get(url, {"next": "/learn/nutri-score/"}).content.decode()
    assert "Back to Nutri-Score, explained" in html
    # other sites are ignored
    html = member_client.get(url, {"next": "https://evil.test/x"}).content.decode()
    assert "evil.test" not in html


def test_account_name_sets_the_greeting(member_client, member):
    from django.urls import reverse

    html = member_client.get(reverse("ask-foodo") + "?tab=account").content.decode()
    assert reverse("account-name") in html
    r = member_client.post(reverse("account-name"), {"first_name": "  Ana   Maria "})
    assert r.status_code == 302
    member.refresh_from_db()
    assert member.first_name == "Ana Maria"
    assert member_client.post(reverse("account-name"), {"first_name": "x" * 31}).status_code == 200


def test_new_note_without_labels_type_optional_and_product_by_name(member_client, member, yogurt):
    page = member_client.get(reverse("ask-foodo"), {"tab": "notes"}).content.decode()
    assert 'placeholder="Your note"' in page and '<option value="" selected>Type</option>' in page
    assert 'list="mo-note-products"' in page and 'placeholder="Product"' in page
    # No type chosen: a plain note; the product picked by its name.
    r = member_client.post(
        reverse("notes-create"), {"text": "Kids like it", "kind": "", "product_name": yogurt.name}
    )
    assert r.status_code == 302
    note = Note.objects.get(user=member, text="Kids like it")
    assert note.kind == Note.Kind.GENERAL and note.product == yogurt
    r = member_client.post(
        reverse("notes-create"), {"text": "x", "product_name": "No such product"}, **HX
    )
    assert "Pick a product from the list." in r.content.decode()
    # One row: pills first, then the note, then the actions.
    page = member_client.get(reverse("ask-foodo"), {"tab": "notes"}).content.decode()
    row = page.split(f'id="note-{note.pk}"')[1].split("</li>")[0]
    assert row.index("note-pills") < row.index("note-text") < row.index("note-actions")

"""Catalogue side rendered on the server (htmx): personal marks, compare selection in the
session, history, the Saved page, the product page's "For you", notes, suggestions, the SVG
compare charts and the scanner page."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.models import Note, ProductMark, ProductVisit, Profile

pytestmark = pytest.mark.django_db

HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def product(off_payload):
    return import_off_product(off_payload())


@pytest.fixture
def products(off_payload):
    return [
        import_off_product(off_payload()),
        import_off_product(off_payload(code="3000000000014", product_name="Oat biscuits")),
        import_off_product(off_payload(code="4000000000020", product_name="Rice cakes")),
        import_off_product(off_payload(code="5900000000008", product_name="Orange juice")),
    ]


def mark_url(p, kind):
    return reverse("product-mark", args=[p.pk, kind])


def kinds(user, p):
    return set(ProductMark.objects.filter(user=user, product=p).values_list("kind", flat=True))


# ---------- Favourites, buy often, avoid, hidden ----------
def test_marks_need_an_account(client, product):
    r = client.post(mark_url(product, "favorite"), {"next": "/products/"})
    assert r.status_code == 302 and r["Location"].startswith(reverse("account_login") + "?next=")
    r = client.post(mark_url(product, "favorite"), **HX)
    assert r.status_code == 204 and r["HX-Redirect"].startswith(reverse("account_login"))
    assert not ProductMark.objects.exists()


def test_favourite_toggle_redirects_back_without_htmx(member_client, member, product):
    r = member_client.post(mark_url(product, "favorite"), {"next": "/products/?q=yo"})
    assert r.status_code == 302 and r["Location"] == "/products/?q=yo"
    assert kinds(member, product) == {"favorite"}
    r = member_client.post(mark_url(product, "favorite"), {"next": "https://evil.test/"})
    assert r["Location"] == reverse("product", args=[product.pk])  # no open redirect
    assert kinds(member, product) == set()


def test_favourite_htmx_partial_and_header_count(member_client, member, product):
    html = member_client.get(reverse("products")).content.decode()
    assert re.search(r'<span class="count" id="saved-count" hidden>', html)
    r = member_client.post(mark_url(product, "favorite"), {"ui": "card"}, **HX)
    body = r.content.decode()
    assert r.status_code == 200
    assert f'id="actions-{product.pk}"' in body and 'aria-pressed="true"' in body
    assert 'id="saved-count" hx-swap-oob="true">1</span>' in body
    assert 'id="messages" class="messages" role="status" hx-swap-oob="true"' in body
    assert "Added to favourites." in body
    html = member_client.get(reverse("products")).content.decode()
    assert 'id="saved-count">1</span>' in html
    # The product page shows the state too.
    page = member_client.get(reverse("product", args=[product.pk])).content.decode()
    assert re.search(r'data-fav aria-pressed="true"', page)


def test_header_count_is_hidden_for_visitors(client):
    html = client.get(reverse("products")).content.decode()
    header = html.split('<header class="site-header">')[1].split("</header>")[0]
    assert 'id="saved-count" hidden' in header and f'href="{reverse("saved")}"' in header


def test_buy_often_and_avoid_are_exclusive(member_client, member, product):
    r = member_client.post(mark_url(product, "buy_often"), {"ui": "page"}, **HX)
    assert kinds(member, product) == {"buy_often"}
    body = r.content.decode()
    assert 'id="product-actions"' in body and 'class="more"' not in body
    assert 'data-list-toggle="buyOften" aria-pressed="true"' in body
    r = member_client.post(mark_url(product, "avoid"), {"ui": "page"}, **HX)
    assert kinds(member, product) == {"avoid"}
    body = r.content.decode()
    assert 'data-list-toggle="avoid" aria-pressed="true"' in body
    assert 'data-list-toggle="buyOften" aria-pressed="false"' in body
    member_client.post(mark_url(product, "avoid"))
    assert kinds(member, product) == set()
    assert member_client.post(mark_url(product, "nonsense")).status_code == 404


def test_hidden_products_are_filtered_and_shown_again(member_client, member, products):
    yogurt = products[0]
    r = member_client.post(mark_url(yogurt, "hidden"), {"ui": "hide"}, **HX)
    assert r.status_code == 200 and "Removed from your list" in r.content.decode()
    html = member_client.get(reverse("products")).content.decode()
    assert reverse("product", args=[yogurt.pk]) not in html
    assert "1 product hidden from your lists." in html
    suggest = member_client.get(reverse("suggest-html"), {"q": "greek"}).content.decode()
    assert "Greek style yogurt" not in suggest
    saved = member_client.get(reverse("saved")).content.decode()
    assert "Greek style yogurt" in saved and "Show hidden again" in saved
    r = member_client.post(reverse("saved-clear", args=["hidden"]))
    assert r.status_code == 302 and not ProductMark.objects.filter(kind="hidden").exists()
    assert (
        reverse("product", args=[yogurt.pk])
        in member_client.get(reverse("products")).content.decode()
    )


def test_products_page_cards_use_details_and_htmx(client, product):
    html = client.get(reverse("products")).content.decode()
    assert f'<details class="card-actions" id="actions-{product.pk}">' in html
    assert "data-hide-product" in html  # visitors get a sign-in link instead of the bin
    assert 'hx-trigger="change from:.filter-select"' in html
    assert 'type="submit">Apply</button>' in html  # still works without JavaScript
    assert 'hx-get="/products/suggest/"' in html


# ---------- Compare selection in the session ----------
def test_compare_selection_add_remove_max_and_clear(client, products):
    a, b, c, d = products
    for p in (a, b, c):
        r = client.post(reverse("compare-add", args=[p.pk]), {"next": "/products/"})
        assert r.status_code == 302
    assert client.session["compare_ids"] == [str(a.pk), str(b.pk), str(c.pk)]
    r = client.post(reverse("compare-add", args=[d.pk]), {"ui": "card"}, **HX)
    body = r.content.decode()
    assert "at most 3 products" in body and str(d.pk) not in client.session["compare_ids"]
    assert 'id="compare-open" href="/compare/" hx-swap-oob="true">' in body
    # /compare/ without ids uses the session selection.
    html = client.get(reverse("compare")).content.decode()
    assert "Greek style yogurt" in html and "Oat biscuits" in html and "Rice cakes" in html
    assert reverse("compare-remove", args=[a.pk]) in html
    r = client.post(reverse("compare-remove", args=[a.pk]), {"ui": "card"}, **HX)
    assert 'data-compare-toggle aria-pressed="false"' in r.content.decode()
    assert client.session["compare_ids"] == [str(b.pk), str(c.pk)]
    # ?ids= keeps working and does not touch the selection.
    html = client.get(reverse("compare"), {"ids": f"{a.pk},{d.pk}"}).content.decode()
    assert "Orange juice" in html and "Greek style yogurt" in html
    assert client.session["compare_ids"] == [str(b.pk), str(c.pk)]
    r = client.post(reverse("compare-clear"))
    assert r.status_code == 302 and "compare_ids" not in client.session
    html = client.get(reverse("compare")).content.decode()
    assert "Add product" in html


def test_compare_link_counts_the_selection(client, products):
    a, b = products[:2]
    client.post(reverse("compare-add", args=[a.pk]))
    html = client.get(reverse("product", args=[b.pk])).content.decode()
    assert re.search(r'id="compare-open" href="/compare/" hidden>', html)
    r = client.post(reverse("compare-add", args=[b.pk]), {"ui": "page"}, **HX)
    body = r.content.decode()
    assert 'id="product-actions"' in body and '<span class="act-label">Added</span>' in body
    assert re.search(r'id="compare-open" href="/compare/" hx-swap-oob="true">.*\(2\)', body)


def test_compare_draws_svg_bars_without_chart_js(client, off_payload):
    def nut(**v):
        return {f"{k}_100g": x for k, x in v.items()}

    a = import_off_product(off_payload(nutriments=nut(sugars=4.7, fat=3.2, proteins=3.5)))
    b = import_off_product(
        off_payload(code="3000000000014", product_name="Oat", nutriments=nut(sugars=21, fat=19))
    )
    client.post(reverse("compare-add", args=[a.pk]))
    client.post(reverse("compare-add", args=[b.pk]))
    html = client.get(reverse("compare")).content.decode()
    assert '<svg class="hbars"' in html and 'class="hbar hbar-s0"' in html
    assert "Side by side" in html
    assert "chart.umd" not in html.lower() and "compare-charts" not in html
    assert "<script" not in html.split("</head>")[1]
    # The longest bar is the largest value (150 px), the other is proportional.
    widths = re.findall(r'class="hbar hbar-s\d" x="108" y="\d+" width="([\d.]+)"', html)
    assert "150" in widths or "150.0" in widths


# ---------- History ----------
def test_history_is_recorded_once_per_reload_window(member_client, member, product):
    url = reverse("product", args=[product.pk])
    member_client.get(url, {"scanned": "5941234567899"})
    member_client.get(url, {"scanned": "5941234567899"})  # reload: not a new scan
    member_client.get(url)  # a plain visit is a different entry
    visits = list(ProductVisit.objects.filter(user=member).order_by("created_at"))
    assert [v.code for v in visits] == ["5941234567899", ""]


def test_visitors_leave_no_history(client, product):
    client.get(reverse("product", args=[product.pk]), {"scanned": "5941234567899"})
    assert not ProductVisit.objects.exists()


# ---------- Saved page ----------
def test_saved_page_lists_favourites_recent_and_hidden(member_client, member, products):
    a, b, c = products[:3]
    member_client.post(mark_url(a, "favorite"))
    member_client.get(reverse("product", args=[b.pk]))
    member_client.post(mark_url(c, "hidden"))
    html = member_client.get(reverse("saved")).content.decode()
    fav = html.split('id="fav-h"')[1].split('id="hist-h"')[0]
    hist = html.split('id="hist-h"')[1].split('id="hidden-h"')[0]
    hidden = html.split('id="hidden-h"')[1]
    assert "Greek style yogurt" in fav and "Oat biscuits" in hist and "Rice cakes" in hidden
    assert mark_url(a, "favorite") in fav and 'hx-target="#saved-lists"' in fav
    # A product in Recently viewed has an outline heart that adds it to favourites.
    assert mark_url(b, "favorite") in hist and "Add to favourites" in hist
    r = member_client.post(reverse("saved-clear", args=["history"]))
    assert r.status_code == 302 and not ProductVisit.objects.filter(user=member).exists()
    # Removing a favourite from the list (htmx) deletes the row.
    r = member_client.post(mark_url(a, "favorite"), {"ui": "row"}, **HX)
    assert r.status_code == 200 and not ProductMark.objects.filter(kind="favorite").exists()


def test_saved_page_heart_moves_a_recent_product_up_to_favourites(member_client, member, products):
    a = products[0]
    member_client.get(reverse("product", args=[a.pk]))
    hist = member_client.get(reverse("saved")).content.decode().split('id="hist-h"')[1]
    assert "Greek style yogurt" in hist
    r = member_client.post(mark_url(a, "favorite"), {"ui": "saved"}, **HX)
    html = r.content.decode()
    assert r.status_code == 200
    fav, hist = html.split('id="fav-h"')[1].split('id="hist-h"')
    assert (
        'id="saved-lists"' in html
        and "Greek style yogurt" in fav
        and "Greek style yogurt" not in hist
    )
    assert ProductMark.objects.filter(user=member, product=a, kind="favorite").exists()
    # Un-favouriting sends it back to Recently viewed.
    html = member_client.post(mark_url(a, "favorite"), {"ui": "saved"}, **HX).content.decode()
    fav, hist = html.split('id="fav-h"')[1].split('id="hist-h"')
    assert "Greek style yogurt" not in fav and "Greek style yogurt" in hist


def test_saved_page_asks_visitors_to_sign_in(client):
    html = client.get(reverse("saved")).content.decode()
    assert "Sign in to save" in html and reverse("account_login") + "?next=%2Fsaved%2F" in html


# ---------- Product page: For you, notes, Add to diary ----------
def test_for_you_uses_the_signed_in_profile(member_client, member, off_payload):
    p = import_off_product(off_payload())  # yogurt: contains milk
    Profile.objects.create(user=member, restrictions=["vegan", "nuts"], nut_types=["en:peanuts"])
    html = member_client.get(reverse("product", args=[p.pk])).content.decode()
    card = html.split('id="foryou-h"')[1].split("</section>")[0]
    assert "For you" in html
    assert 'class="rs rs-incompatible"><strong>Vegan: Not compatible. </strong>' in card
    assert "Peanuts:" in card
    assert "Checked for: Vegan, 1 allergy." in card
    assert 'href="/ask-foodo/profile/#restrictions"' in card
    assert "Never a guarantee" in card
    assert "of your daily targets" not in html  # the portion block is gone
    # No "Ask Foodo about this product" button on product pages any more.
    assert "Ask Foodo about this product" not in html and "ask-standalone" not in html


def test_for_you_without_preferences_and_for_visitors(member_client, product):
    html = member_client.get(reverse("product", args=[product.pk])).content.decode()
    card = html.split('id="foryou-h"')[1].split("</section>")[0]
    # Nothing set: one line, to the settings.
    assert (
        card.count('class="foryou-line"') == 1 and 'href="/ask-foodo/profile/#restrictions"' in card
    )
    assert "Add your diet or allergies to check this product" in card and "Checked for" not in card
    anon = Client().get(reverse("product", args=[product.pk])).content.decode()
    card = anon.split('id="foryou-h"')[1].split("</section>")[0]
    assert "Sign in to check this product against your diet and allergies" in card
    assert reverse("account_login") + "?next=" in card
    assert "Ask Foodo about this product" not in anon
    assert 'id="ola-data"' not in anon and "Sign in to save" in anon


def test_product_page_add_to_diary_is_a_link(client, product):
    html = client.get(reverse("product", args=[product.pk])).content.decode()
    assert f'href="{reverse("add-food", args=[product.pk])}"' in html and "Add to diary" in html
    assert 'id="product-actions"' in html and 'class="act-label">Compare<' in html


def test_product_note(member_client, member, product):
    url = reverse("product-note", args=[product.pk])
    r = Client().post(url, {"text": "Nice"})
    assert r.status_code == 302 and r["Location"].startswith(reverse("account_login"))
    r = member_client.post(url, {"text": "  Tastes great  ", "kind": "taste"}, **HX)
    body = r.content.decode()
    assert r.status_code == 200 and 'id="note"' in body and "Note saved" in body
    note = Note.objects.get(user=member)
    assert (note.text, note.kind, note.product_id) == ("Tastes great", "taste", product.pk)
    r = member_client.post(url, {"text": "", "kind": "bogus"})
    assert r.status_code == 302 and r["Location"].endswith("?note=1#note")
    assert Note.objects.count() == 1
    html = member_client.get(reverse("product", args=[product.pk]), {"note": "1"}).content.decode()
    assert '<details class="card note-card" id="note" open>' in html


# ---------- Search suggestions ----------
def test_suggest_html_links_and_diary_mode(client, product):
    html = client.get(reverse("suggest-html"), {"q": "g"}).content.decode()
    assert "suggest-item" not in html  # fewer than 2 characters: nothing
    html = client.get(reverse("suggest-html"), {"q": "greek"}).content.decode()
    assert f'href="{reverse("product", args=[product.pk])}"' in html
    assert "See all results for" in html and "<script" not in html
    html = client.get(
        reverse("suggest-html"), {"q": "greek", "add": "lunch", "date": "2026-11-18"}
    ).content.decode()
    assert reverse("add-food", args=[product.pk]) + "?meal=lunch&amp;date=2026-11-18" in html
    html = client.get(reverse("suggest-html"), {"q": "zzzz"}).content.decode()
    assert "No products match" in html


# ---------- Scanner, carousel, CSP ----------
def test_scanner_page_keeps_its_data_attributes(client):
    html = client.get(reverse("scan"), {"add": "dinner", "date": "2026-11-18"}).content.decode()
    assert "js/scanner.js" in html and "data-scanner" in html
    assert f'data-lookup-url="{reverse("lookup")}"' in html
    assert 'data-diary-query="add=dinner&amp;date=2026-11-18"' in html
    assert "data-zxing-url=" in html and "data-zxing-wasm=" in html


def test_lookup_accepts_camera_format_names(client, product):
    for fmt in ("ean_13", "EAN13", "EAN-13"):
        r = client.get(reverse("lookup"), {"code": "5941234567899", "format": fmt})
        assert r.status_code == 302, fmt


def test_home_is_static_html(client):
    html = client.get(reverse("home")).content.decode()
    assert 'class="meal-tiles try-tiles stagger"' in html and 'href="/learn/"' in html
    assert "home.js" not in html and 'style="' not in html


def test_no_inline_scripts_styles_or_removed_js(member_client, products):
    client = Client()
    a, b = products[:2]
    client.post(reverse("compare-add", args=[a.pk]))
    client.post(reverse("compare-add", args=[b.pk]))
    member_client.post(mark_url(a, "favorite"))
    pages = [
        (client, reverse("home")),
        (client, reverse("scan")),
        (client, reverse("products")),
        (client, reverse("product", args=[a.pk])),
        (client, reverse("compare")),
        (client, reverse("saved")),
        (member_client, reverse("product", args=[a.pk])),
        (member_client, reverse("saved")),
        (member_client, reverse("products")),
    ]
    for c, url in pages:
        html = c.get(url).content.decode()
        assert 'style="' not in html, url
        assert "hx-on" not in html and "js/app.js" not in html, url
        for tag in re.findall(r"<script[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (url, tag)


def test_ocr_status_fragment_polls_and_stops(client, product):
    import io
    import time

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from contributions.models import OCRRun
    from contributions.services import drafts

    c = drafts.create_draft(session_key="s", problem_type="missing_data", product=product)
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 220, 210)).save(buf, format="JPEG")
    drafts.add_image(c, SimpleUploadedFile("p.jpg", buf.getvalue(), "image/jpeg"), "ingredients")
    run = OCRRun.objects.get(image__contribution=c)
    url = reverse("contribution-ocr-status", args=[c.access_token])
    edit = client.get(reverse("contribution", args=[c.access_token]), {"step": "check"})
    assert 'hx-trigger="every 3s"' in edit.content.decode()
    r = client.get(url, {"started": int(time.time())}, **HX)
    assert r.status_code == 200 and 'hx-trigger="every 3s"' in r.content.decode()
    r = client.get(url, {"started": int(time.time()) - 300}, **HX)  # 2-minute cap
    assert r.status_code == 286 and "hx-trigger" not in r.content.decode()
    run.status = "done"
    run.save()
    r = client.get(url, {"started": int(time.time())}, **HX)
    assert r.status_code == 286 and "Fill the form with recognised values" in r.content.decode()
    assert client.get(url).json()["runs"][0]["status"] == "done"  # JSON kept for API use


def test_photo_slot_uploads_on_change_with_htmx(client, product):
    from contributions.services import drafts

    c = drafts.create_draft(session_key="s", problem_type="missing_data", product=product)
    html = client.get(reverse("contribution", args=[c.access_token])).content.decode()
    assert 'hx-trigger="change"' in html and "data-autoupload" not in html
    r = client.post(reverse("contribution-upload", args=[c.access_token]), {}, **HX)
    assert r.status_code == 204 and "step=photos" in r["HX-Redirect"]


@pytest.mark.django_db
def test_search_box_does_not_inherit_the_filter_form_htmx_settings(client):
    """The suggestions must not pick up the form's hx-select/hx-push-url (they would be
    swallowed and the address bar would change on every key)."""
    html = client.get(reverse("products")).content.decode()
    form = html.split('class="filters"')[1].split(">")[0]
    assert 'hx-disinherit="*"' in form

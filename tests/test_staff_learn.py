"""Staff panel: the Learn CMS (articles with SEO fields, topics)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from core.models import LearnArticle, LearnTopic

pytestmark = pytest.mark.django_db


@pytest.fixture
def editor(client):
    call_command("setup_roles")
    user = get_user_model().objects.create_user(
        "ed", "ed@example.com", "x-password-123", is_staff=True
    )
    user.groups.add(Group.objects.get(name="Editors"))
    client.force_login(user)
    return user


def test_only_editors_open_the_cms(client, member):
    client.force_login(member)
    assert client.get(reverse("staff:learn")).status_code == 403
    staff = get_user_model().objects.create_user(
        "s", "s@example.com", "x-password-123", is_staff=True
    )
    client.force_login(staff)  # staff without the Learn permission
    assert client.get(reverse("staff:learn")).status_code == 403


def test_list_create_edit_and_delete(client, editor):
    html = client.get(reverse("staff:learn")).content.decode()
    assert "Nutri-Score, explained" in html and "New article" in html
    topic = LearnTopic.objects.get(key="labels")
    data = {
        "title": "Fibre on the label", "slug": "", "topic": topic.pk,
        "summary": "Why fibre matters.", "body": "## Why\nIt helps.", "minutes": 2,
        "seo_title": "Fibre on food labels", "meta_description": "What fibre per 100 g tells you.",
        "canonical_url": "", "published": "on", "order": 5,
    }  # fmt: skip
    r = client.post(reverse("staff:learn-new"), data)
    a = LearnArticle.objects.get(slug="fibre-on-the-label")
    assert r.status_code == 302 and a.seo_title == "Fibre on food labels"
    html = client.get(reverse("staff:learn-article", args=[a.pk])).content.decode()
    assert "Search result preview" in html and "Fibre on food labels" in html
    page = client.get(reverse("learn-article", args=[a.slug])).content.decode()
    assert '<meta name="description" content="What fibre per 100 g tells you.">' in page
    # the same address cannot be used twice
    r = client.post(reverse("staff:learn-new"), {**data, "slug": "nutri-score"})
    assert r.status_code == 200 and "already uses this address" in r.content.decode()
    r = client.post(reverse("staff:learn-delete", args=[a.pk]))
    assert r.status_code == 302 and not LearnArticle.objects.filter(pk=a.pk).exists()


def test_topics_validate_icons(client, editor):
    r = client.post(
        reverse("staff:learn-topics"),
        {"title": "Labels for kids", "key": "", "icon": "nope", "order": 9},
    )
    assert r.status_code == 200 and "Unknown icon" in r.content.decode()
    r = client.post(
        reverse("staff:learn-topics"),
        {"title": "Labels for kids", "key": "", "icon": "leaf", "order": 9},
    )
    assert r.status_code == 302 and LearnTopic.objects.filter(key="labels-for-kids").exists()


# ---------- Products in articles ----------
@pytest.fixture
def yogurt(off_payload):
    from catalog.services.importer import import_off_product

    return import_off_product(off_payload())


def test_link_products_from_our_catalogue(client, editor, yogurt):
    a = LearnArticle.objects.get(slug="read-a-label")
    html = client.get(reverse("staff:learn-products", args=[a.pk]), {"q": "greek"}).content.decode()
    assert "Greek style yogurt" in html and 'name="product"' in html
    client.post(reverse("staff:learn-products-add", args=[a.pk]), {"product": str(yogurt.pk)})
    assert list(a.products.all()) == [yogurt]
    page = client.get(reverse("learn-article", args=[a.slug])).content.decode()
    assert "Products in this article" in page and f"/p/{yogurt.pk}/" in page
    client.post(reverse("staff:learn-products-remove", args=[a.pk, yogurt.pk]))
    assert not a.products.exists()


def test_search_open_food_facts(client, editor, monkeypatch):
    from off.client import OFFClient

    rows = [{"code": "3000000000012", "product_name": "Oat drink", "brands": "Oaty"}]
    monkeypatch.setattr(OFFClient, "search_text", lambda self, q, page_size=10: rows)
    a = LearnArticle.objects.get(slug="nova")
    url = reverse("staff:learn-products", args=[a.pk])
    html = client.get(url, {"q": "oat", "source": "off"}).content.decode()
    assert "Oat drink" in html and 'name="code" value="3000000000012"' in html


def test_article_photo_becomes_a_small_metadata_free_webp(client, editor, tmp_path, settings):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make: must not survive
    Image.new("RGB", (4000, 3000), (40, 120, 90)).save(buf, "JPEG", exif=exif)
    topic = LearnTopic.objects.get(key="labels")
    data = {
        "title": "Photo test", "slug": "", "topic": topic.pk, "summary": "S.", "body": "B.",
        "minutes": 2, "published": "on", "order": 1,
        "image": SimpleUploadedFile("big.jpg", buf.getvalue(), "image/jpeg"),
    }  # fmt: skip
    assert client.post(reverse("staff:learn-new"), data).status_code == 302
    a = LearnArticle.objects.get(slug="photo-test")
    assert a.image.name.endswith(".webp")
    with a.image.open("rb") as f, Image.open(f) as img:
        assert img.format == "WEBP" and img.size == (2000, 1500)
        assert not img.getexif()
    r = client.get(reverse("learn-image", args=[a.slug]))
    assert r["Content-Type"] == "image/webp"


def test_publishing_an_article_notifies_once(client, editor, member):
    import datetime as dt

    from django.utils import timezone

    from myola.models import Notification
    from myola.tasks import notify_new_articles

    LearnArticle.objects.update(notified_at=timezone.now())  # the seeded ones are old news
    topic = LearnTopic.objects.get(key="labels")
    data = {"title": "Salt in bread", "slug": "", "topic": topic.pk, "summary": "S.",
            "body": "B.", "minutes": 2, "published": "on", "order": 1}  # fmt: skip
    r = client.post(reverse("staff:learn-new"), data, follow=True)
    assert "notifications shortly" in r.content.decode()
    notify_new_articles()  # what the queued job runs
    n = Notification.objects.get(user=member, key__startswith="learn:")
    assert n.title == "New article in the Magazine" and n.url == "/learn/salt-in-bread/"
    assert n.body == "“Salt in bread”: a 2-minute read in Reading the label."
    a = LearnArticle.objects.get(slug="salt-in-bread")
    client.post(reverse("staff:learn-article", args=[a.pk]), {**data, "summary": "Edited."})
    assert notify_new_articles() == 0  # edits never notify again
    # A future date waits for its day.
    later = (timezone.now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    client.post(reverse("staff:learn-new"), {**data, "title": "Soon", "published_at": later})
    assert notify_new_articles() == 0
    assert not Notification.objects.filter(body__contains="“Soon”").exists()


@pytest.mark.django_db
def test_magazine_cover_month_photo_and_place(editor, client, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from core.models import MagazineCover

    settings.MEDIA_ROOT = tmp_path
    html = client.get(reverse("learn")).content.decode()
    assert "<span data-cover-month>November 2026</span>" in html  # from the migration
    assert "cover-head.webp" in html  # the built-in photo and its head cut-out

    buf = io.BytesIO()
    Image.new("RGB", (1400, 626), "#0e290d").save(buf, "PNG")
    photo = SimpleUploadedFile("c.png", buf.getvalue(), content_type="image/png")
    data = {"month": "2027-01", "photo_size": "100", "photo_x": "-112", "photo_y": "4",
            "mobile_x": "65", "mobile_y": "20", "mobile_zoom": "110"}  # fmt: skip
    r = client.post(reverse("staff:learn-covers"), {**data, "image": photo})
    assert r.status_code == 302
    assert MagazineCover.objects.count() == 1  # one issue, no archive
    cover = MagazineCover.load()
    assert cover.month.isoformat() == "2027-01-01" and cover.image.name.endswith(".webp")

    html = client.get(reverse("learn")).content.decode()
    assert "<span data-cover-month>January 2027</span>" in html
    assert "cover-head.webp" not in html and 'src="/learn/cover/photo/?v=' in html
    assert client.get(reverse("learn-cover-image"))["Content-Type"] == "image/webp"
    css = client.get(reverse("learn-cover-style")).content.decode()
    assert (
        css == ".mag-cover{--photo-h:100;--photo-x:-112;--photo-y:4;--m-x:65;--m-y:20;--m-zoom:110}"
    )

    r = client.post(reverse("staff:learn-covers"), {**data, "photo_size": "900"})
    assert r.status_code == 200  # out of range: not saved
    page = client.get(reverse("staff:learn-covers")).content.decode()
    assert 'form="cover-form"' in page and 'data-photo="mx"' in page
    # The phone preview: the only page that may be framed, by this site only.
    r = client.get(reverse("staff:learn-cover-preview"))
    assert "frame-ancestors 'self'" in r["Content-Security-Policy"]
    assert r["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'none'" in client.get(reverse("learn"))["Content-Security-Policy"]

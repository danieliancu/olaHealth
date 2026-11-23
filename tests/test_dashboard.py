"""Soft redesign: Today dashboard (greeting, one nudge, meal tiles, quick actions), Ask Foodo tab,
Learn pages and the My Track stats and achievements."""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pytest
from django.urls import reverse

from catalog.services.importer import import_off_product
from myola.models import ChatMessage, DiaryEntry, ProductVisit, WaterEntry
from myola.services import nudges, stats
from myola.services.profiles import get_profile, local_today
from tests.test_today import make_entry

HX = {"HTTP_HX_REQUEST": "true"}


def _groups(logged=()):
    return [
        SimpleNamespace(key=k, label=k.title(), icon="utensils", entries=[1] if k in logged else [],
                        scan_url=f"/scan/?add={k}")
        for k in ("breakfast", "lunch", "dinner", "snacks")
    ]  # fmt: skip


def _at(hour):
    return dt.datetime(2026, 11, 20, hour, 0)


# ---------- Nudges ----------
def test_greeting_follows_the_hour():
    assert nudges.greeting(_at(8)) == "Good morning"
    assert nudges.greeting(_at(14)) == "Good afternoon"
    assert nudges.greeting(_at(22)) == "Good evening"


def test_no_nudge_on_past_days():
    assert (
        nudges.pick_nudge(is_today=False, now=_at(20), groups=_groups(), water_ml=0, scan_url="/s")
        is None
    )


def test_nothing_logged_comes_first():
    n = nudges.pick_nudge(is_today=True, now=_at(10), groups=_groups(), water_ml=0, scan_url="/s")
    assert n.key == "start" and n.url == "/s"


def test_latest_missed_meal_and_never_calories():
    n = nudges.pick_nudge(
        is_today=True, now=_at(16), groups=_groups({"breakfast"}), water_ml=500, scan_url="/s"
    )
    assert n.key == "meal-lunch" and n.url == "/scan/?add=lunch"
    assert "kcal" not in n.text and "calorie" not in n.text.lower()


def test_water_nudge_and_quiet_day():
    logged = {"breakfast", "lunch"}
    n = nudges.pick_nudge(
        is_today=True, now=_at(13), groups=_groups(logged), water_ml=0, scan_url="/s"
    )
    assert n.key == "water"
    assert (
        nudges.pick_nudge(
            is_today=True, now=_at(13), groups=_groups(logged), water_ml=250, scan_url="/s"
        )
        is None
    )


# ---------- Today page ----------
@pytest.mark.django_db
def test_today_dashboard_sections(member_client, member):
    member.first_name = "Ana"
    member.save()
    html = member_client.get(reverse("today")).content.decode()
    assert ", Ana</p>" in html
    assert ">Today</h1>" in html
    assert 'class="meal-tiles stagger"' in html
    assert len(re.findall(r'class="meal-tile[ "]', html)) == 4
    assert 'class="qa qa-primary"' in html and 'href="/learn/"' in html
    assert 'class="cheer"' in html
    assert 'style="' not in html
    assert "<script" not in html.split("</head>")[1]


@pytest.mark.django_db
def test_meal_tiles_hide_calories_with_eating_disorder(member_client, member):
    profile = get_profile(member)
    profile.conditions = ["eating_disorder"]
    profile.save()
    make_entry(member, meal="lunch")
    html = member_client.get(reverse("today")).content.decode()
    tiles = html.split('class="meal-tiles stagger"')[1].split("</ul>")[0]
    assert "kcal" not in tiles and "1 food" in tiles


@pytest.mark.django_db
def test_htmx_swap_marks_today_as_swapped(member_client, member):
    day = local_today(get_profile(member)).isoformat()
    html = member_client.post(
        reverse("today-water-add"), {"date": day, "ml": "250"}, **HX
    ).content.decode()
    assert 'class="today is-swapped" id="today"' in html


# ---------- Ask Foodo tab ----------
@pytest.mark.django_db
def test_chat_page_is_the_active_tab(member_client):
    html = member_client.get(reverse("chat")).content.decode()
    nav = html.split('<nav class="tabbar"')[1].split("</nav>")[0]
    current = re.findall(r'<a href="([^"]+)" aria-current="page">', nav)
    assert current == ["/ask-foodo/chat/"]
    assert 'class="chat-suggest"' not in html  # no suggestion pills


# ---------- Learn ----------
@pytest.mark.django_db
def test_learn_index_topic_and_article(client):
    html = client.get(reverse("learn")).content.decode()
    # The cover: "Magazine", the tagline, the topic pills (All + every topic, in order),
    # Editor's picks (3), "Explore the latest" (4) and the phone cover card.
    assert ">Foodo Magazine</h1>" in html and "Good food, clearly understood" not in html
    # The topics: in the phone cover card (each title on two lines) and listed on desktop.
    card = html.split('class="mag-issue-card"')[1].split("</nav>")[0]
    topics = re.findall(r'href="/learn/topic/([\w-]+)/"', card)
    assert topics == ["labels", "scores", "ingredients", "allergens"]
    assert "<span>Reading<br>the label</span>" in card and "<span>Allergens</span>" in card
    listed = html.split('class="mag-topic-list"')[1].split("</ul>")[0]
    assert re.findall(r'href="/learn/topic/([\w-]+)/"', listed) == topics
    assert ">All<" not in html
    picks = html.split('id="mag-picks-h"')[1].split("</section>")[0]
    latest = html.split('id="mag-latest-h"')[1].split("</section>")[0]
    assert picks.count('class="mag-pick"') == 3 and latest.count('class="mag-card"') == 4
    assert "min read" in picks and "<img" in picks  # picks: photo and reading time
    assert "mag-card-img" in latest and "min read" not in latest  # photo on desktop only
    assert "FOODO" not in html  # the card holds only the photo and the topics
    assert 'style="' not in html  # no inline styles (CSP)
    r = client.get(reverse("learn") + "?topic=allergens")
    assert r.status_code == 301 and r["Location"] == "/learn/topic/allergens/"
    html = client.get(reverse("learn-topic", args=["allergens"])).content.decode()
    assert 'href="/learn/allergens-traces/"' in html and "<h1>Allergens</h1>" in html
    assert 'class="article-date"' in html and 'style="' not in html
    assert client.get(reverse("learn-topic", args=["nope"])).status_code == 404
    html = client.get(reverse("learn-article", args=["nutri-score"])).content.decode()
    assert "Nutri-Score, explained" in html and "not medical advice" not in html
    assert "<script" not in html.split("</head>")[1]
    assert client.get(reverse("learn") + "?topic=nope").status_code == 200
    assert client.get(reverse("learn-article", args=["nope"])).status_code == 404


# ---------- Stats and achievements ----------
@pytest.fixture
def yogurt(off_payload):
    return import_off_product(off_payload())


@pytest.mark.django_db
def test_stats_and_achievements(member, yogurt):
    profile = get_profile(member)
    today = local_today(profile)
    for i in range(7):
        make_entry(member, day=today - dt.timedelta(days=i))
    for meal in ("breakfast", "dinner"):
        make_entry(member, day=today, meal=meal)
    WaterEntry.objects.create(user=member, date=today, ml=1400)
    ProductVisit.objects.create(user=member, product=yogurt, code="123")
    ChatMessage.objects.create(user=member, role=ChatMessage.Role.USER, text="hi")

    out = stats.overview_stats(member, profile, today)
    values = {s.key: s.value for s in out["stats"]}
    assert values == {"days": "7/30", "products": "1", "water": "0.2 l", "active": "0"}
    keys = {a.key for a in out["achievements"]}
    assert {"first-scan", "first-question", "first-day", "full-day", "seven-days"} <= keys


@pytest.mark.django_db
def test_stats_stay_neutral_with_eating_disorder(member):
    profile = get_profile(member)
    profile.conditions = ["eating_disorder"]
    profile.save()
    make_entry(member)
    out = stats.overview_stats(member, profile, local_today(profile))
    assert "days" not in {s.key for s in out["stats"]}
    assert not {"first-day", "full-day", "seven-days"} & {a.key for a in out["achievements"]}
    assert DiaryEntry.objects.filter(user=member).exists()


@pytest.mark.django_db
def test_my_track_overview_shows_stats(member_client):
    html = member_client.get(reverse("ask-foodo")).content.decode()
    assert "Your stats" in html and html.count('class="stat-card"') == 4
    assert 'href="/learn/"' in html


def test_meter_level_steps():
    from myola.services.diary import meter_level

    assert [meter_level(v, 100) for v in (20, 60, 100, 120, 200)] == [
        "low",
        "mid",
        "on",
        "high",
        "over",
    ]
    assert meter_level(50, None) == ""


# ---------- Learn CMS ----------
@pytest.mark.django_db
def test_learn_cms_seo_and_unpublished(client, admin_user):
    from core.models import LearnArticle

    html = client.get(reverse("learn-article", args=["nutri-score"])).content.decode()
    head = html.split("</head>")[0]
    assert '<meta name="description"' in head and 'rel="canonical"' in head and "og:title" in head
    assert "<h2>What it measures</h2>" in html and "<strong>A</strong>" in html
    a = LearnArticle.objects.get(slug="nova")
    a.published = False
    a.save()
    assert client.get(reverse("learn-article", args=["nova"])).status_code == 404
    client.force_login(admin_user)  # staff can preview, marked noindex
    html = client.get(reverse("learn-article", args=["nova"])).content.decode()
    assert '<meta name="robots" content="noindex">' in html


@pytest.mark.django_db
def test_learn_markup_escapes_html():
    from core.markup import render

    out = render("<script>x</script> **b** [a](/learn/) [x](javascript:alert(1))")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<strong>b</strong>" in out and '<a href="/learn/">a</a>' in out
    assert "javascript:" not in out.split("href=")[-1] or 'href="javascript' not in out


@pytest.mark.django_db
def test_sitemap_lists_learn_articles(client):
    xml = client.get("/sitemap.xml").content.decode()
    assert "/learn/nutri-score/" in xml and "/learn/" in xml


@pytest.mark.django_db
def test_achievements_show_an_even_number_on_desktop(member, yogurt):
    profile = get_profile(member)
    today = local_today(profile)
    ProductVisit.objects.create(user=member, product=yogurt, code="123")  # first scan
    ChatMessage.objects.create(user=member, role=ChatMessage.Role.USER, text="hi")  # first question
    make_entry(member, day=today)  # first day
    out = stats.overview_stats(member, profile, today)
    assert len(out["achievements"]) == 3 and out["ach_shown"] == 2


@pytest.mark.django_db
def test_magazine_picks_are_editors_picks_and_latest_are_newest(client):
    from core.models import LearnArticle

    def slugs(section):
        html = client.get(reverse("learn")).content.decode()
        block = html.split(f'id="mag-{section}-h"')[1].split("</section>")[0]
        return re.findall(r'class="mag-(?:pick|card)" href="/learn/([\w-]+)/"', block)

    published = LearnArticle.objects.filter(published=True)
    published.update(editors_pick=False)
    chosen = list(published.order_by("published_at")[:2])  # two older articles, picked
    LearnArticle.objects.filter(pk__in=[a.pk for a in chosen]).update(editors_pick=True)
    picks = slugs("picks")
    assert picks[:2] == [a.slug for a in sorted(chosen, key=lambda a: a.published_at, reverse=True)]
    assert len(picks) == 3  # topped up with the newest article
    latest = slugs("latest")
    # The newest articles that are not picks first; only when there are too few others, the
    # newest picks fill the remaining places.
    order = [a.slug for a in published.order_by("-published_at")]
    expected = ([s for s in order if s not in picks] + [s for s in order if s in picks])[:4]
    assert len(latest) == 4 and latest == expected
    # Unpublished articles never show.
    hidden = LearnArticle.objects.get(slug=picks[0])
    hidden.published = False
    hidden.save()
    assert hidden.slug not in slugs("picks") + slugs("latest")


@pytest.mark.django_db
def test_magazine_search_page(client):
    r = client.get(reverse("learn") + "?q=sugar")
    assert r.status_code == 302 and r["Location"] == "/learn/search/?q=sugar"
    html = client.get(reverse("learn-search"), {"q": "Nutri-Score"}).content.decode()
    assert 'href="/learn/nutri-score/"' in html and 'href="/learn/">' in html
    html = client.get(reverse("learn-search"), {"q": "zzzz"}).content.decode()
    assert "No article matches that search yet." in html


@pytest.mark.django_db
def test_daily_summary_day_arrows(member_client, member):
    today = local_today(get_profile(member))
    html = member_client.get(reverse("today")).content.decode()
    nav = html.split('class="sum-nav"')[1].split("</div>")[0]
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    assert f'href="/today/?date={yesterday}"' in nav and "sum-arrow is-disabled" in nav
    html = member_client.get(reverse("today"), {"date": yesterday}).content.decode()
    nav = html.split('class="sum-nav"')[1].split("</div>")[0]
    assert f'href="/today/?date={today.isoformat()}"' in nav and "is-disabled" not in nav


@pytest.mark.django_db
def test_magazine_search_suggestions(client):
    html = client.get(reverse("learn")).content.decode()
    assert 'hx-get="/learn/suggest/"' in html and 'id="mag-suggest"' in html
    assert html.index('class="mag-search"') < html.index('id="mag-latest-h"')
    # Desktop: a search button beside "Explore the latest", to the search page.
    head = html.split('class="mag-latest-head"')[1].split("</div>")[0]
    assert 'class="mag-search-btn" href="/learn/search/"' in head
    assert client.get(reverse("learn-suggest"), {"q": "s"}).content.decode().strip() == ""
    html = client.get(reverse("learn-suggest"), {"q": "sugar"}).content.decode()
    titles = re.findall(r'class="mag-suggest-title">([^<]+)<', html)
    assert 0 < len(titles) <= 6 and "sugar" in titles[0].lower()  # title matches first
    assert 'href="/learn/search/?q=sugar"' in html
    html = client.get(reverse("learn-suggest"), {"q": "zzzz"}).content.decode()
    assert "No article matches" in html

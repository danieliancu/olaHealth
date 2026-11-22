"""Learn: short articles about reading food labels, managed in the admin (core.LearnArticle).

The physical label stays the reference. Articles carry their
own SEO fields (title, description, canonical, noindex); the pages render them in <head>.
"""

from __future__ import annotations

from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET

from core.markup import render as render_markup
from core.markup import render_halves
from core.models import LearnArticle, LearnTopic, MagazineCover

IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp"}


def _visible(request: HttpRequest):
    qs = LearnArticle.objects.select_related("topic")
    if not (request.user.is_authenticated and request.user.is_staff):
        qs = qs.filter(published=True)
    return qs


# The cover: topic places fixed by topic order, as on the printed cover — left column: topics 1
# (top) and 4; right column: topics 3 (top) and 2. Their titles change with what staff feature.
COVER_SIDES = (("left", (0, 3)), ("right", (2, 1)))
COVER_PER_TOPIC = 2


def _cover_sides() -> list[dict]:
    """Two columns of topic slots. Each slot: the topic and up to 2 featured articles (newest
    first); a topic without a featured article shows its newest one."""
    visible = LearnArticle.objects.filter(published=True).order_by("-published_at")
    topics = list(LearnTopic.objects.all()[:4])
    sides = []
    for name, indexes in COVER_SIDES:
        slots = []
        for i in indexes:
            if i >= len(topics):
                continue
            mine = visible.filter(topic=topics[i])
            articles = list(mine.filter(featured=True)[:COVER_PER_TOPIC]) or list(mine[:1])
            slots.append({"topic": topics[i], "articles": articles, "lead": i == 0})
        sides.append({"name": name, "slots": slots})
    return sides


@require_GET
def learn_index(request: HttpRequest) -> HttpResponse:
    """The Magazine cover. Old ?topic= and ?q= links go to the topic or search page."""
    topic_key = request.GET.get("topic", "")
    q = request.GET.get("q", "").strip()[:80]
    if q:
        return redirect(f"{reverse('learn-search')}?{urlencode({'q': q})}")
    topic = LearnTopic.objects.filter(key=topic_key).first() if topic_key else None
    if topic:
        return redirect("learn-topic", key=topic.key, permanent=True)
    return render(request, "learn/index.html", cover_context())


def cover_context() -> dict:
    """What the cover needs (also Staff → Magazine → Cover, for its preview)."""
    issue = MagazineCover.load()
    return {
        "sides": _cover_sides(),
        "nav_active": "learn",
        "issue_month": issue.month,  # "MAGAZINE • NOVEMBER 2026"
        # An uploaded photo replaces the built-in one (and its head-over-the-logo cut-out).
        "cover_url": issue.image_url(),
        "cover_style_url": issue.style_url(),
    }


@require_GET
def learn_search(request: HttpRequest) -> HttpResponse:
    """Search results: title, summary, body or topic name."""
    q = request.GET.get("q", "").strip()[:80]
    articles = []
    if q:
        articles = list(
            _visible(request)
            .filter(
                Q(title__icontains=q)
                | Q(summary__icontains=q)
                | Q(body__icontains=q)
                | Q(topic__title__icontains=q)
            )
            .order_by("-published_at")
        )
    return render(
        request, "learn/search.html", {"q": q, "articles": articles, "nav_active": "learn"}
    )


SUGGEST_LIMIT = 6


@require_GET
def learn_suggest(request: HttpRequest) -> HttpResponse:
    """Suggestions under the Magazine cover's search box, as the user types (htmx): titles
    first, then the summary, body and topic; at most ``SUGGEST_LIMIT``."""
    q = " ".join(request.GET.get("q", "").split())[:80]
    articles = []
    if len(q) >= 2:
        found = (
            _visible(request)
            .filter(
                Q(title__icontains=q)
                | Q(summary__icontains=q)
                | Q(body__icontains=q)
                | Q(topic__title__icontains=q)
            )
            .select_related("topic")
            .order_by("-published_at")
        )
        articles = sorted(found[:40], key=lambda a: q.lower() not in a.title.lower())
        articles = articles[:SUGGEST_LIMIT]
    return render(request, "learn/partials/suggest.html", {"q": q, "articles": articles})


@require_GET
def learn_topic(request: HttpRequest, key: str) -> HttpResponse:
    """One topic: its title, then its articles, newest first."""
    topic = get_object_or_404(LearnTopic, key=key)
    articles = _visible(request).filter(topic=topic).order_by("-published_at")
    return render(
        request,
        "learn/topic.html",
        {"topic": topic, "articles": list(articles), "nav_active": "learn"},
    )


@require_GET
def learn_article(request: HttpRequest, slug: str) -> HttpResponse:
    article = _visible(request).filter(slug=slug).first()
    if article is None:
        raise Http404
    related = _visible(request).filter(topic=article.topic).exclude(pk=article.pk)
    return render(
        request,
        "learn/article.html",
        {
            "article": article,
            "topic": article.topic,
            "body": render_markup(article.body),
            "body_parts": render_halves(article.body),
            "related": list(related),
            "products": list(article.products.filter(current_revision__isnull=False)[:12]),
            "canonical": article.canonical_url or request.build_absolute_uri(request.path),
            "nav_active": "learn",
        },
    )


@require_GET
def learn_cover_image(request: HttpRequest) -> FileResponse:
    """The uploaded Magazine cover photo (private storage)."""
    cover = MagazineCover.load()
    if not cover.image:
        raise Http404
    response = FileResponse(cover.image.open("rb"), content_type="image/webp")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def learn_cover_style(request: HttpRequest) -> HttpResponse:
    """Where the cover photo sits on desktop and phones (a tiny stylesheet)."""
    cover = MagazineCover.load()
    response = HttpResponse(cover.style_css(), content_type="text/css")
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def learn_image(request: HttpRequest, slug: str) -> FileResponse:
    """Uploaded article photos (private storage, served only for published articles)."""
    article = get_object_or_404(LearnArticle, slug=slug, published=True)
    if not article.image:
        raise Http404
    ext = "." + article.image.name.rsplit(".", 1)[-1].lower()
    response = FileResponse(
        article.image.open("rb"), content_type=IMAGE_TYPES.get(ext, "image/jpeg")
    )
    response["Cache-Control"] = "public, max-age=86400"
    return response

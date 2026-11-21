"""Learn: short articles about reading food labels, managed in the admin (core.LearnArticle).

Information, not medical advice; the physical label stays the reference. Articles carry their
own SEO fields (title, description, canonical, noindex); the pages render them in <head>.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from core.markup import render as render_markup
from core.markup import render_halves
from core.models import LearnArticle, LearnTopic

IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp"}


def _visible(request: HttpRequest):
    qs = LearnArticle.objects.select_related("topic")
    if not (request.user.is_authenticated and request.user.is_staff):
        qs = qs.filter(published=True)
    return qs


def _topics_with_counts() -> list[dict]:
    topics = LearnTopic.objects.annotate(n=Count("articles", filter=Q(articles__published=True)))
    return [{"topic": t, "count": t.n} for t in topics]


@require_GET
def learn_index(request: HttpRequest) -> HttpResponse:
    topic_key = request.GET.get("topic", "")
    topic = LearnTopic.objects.filter(key=topic_key).first() if topic_key else None
    if topic and not request.GET.get("q"):
        return redirect("learn-topic", key=topic.key, permanent=True)  # old ?topic= links
    q = request.GET.get("q", "").strip()[:80]
    articles = _visible(request)
    if q:
        articles = articles.filter(
            Q(title__icontains=q)
            | Q(summary__icontains=q)
            | Q(body__icontains=q)
            | Q(topic__title__icontains=q)
        )
    elif topic:
        articles = articles.filter(topic=topic)
    else:
        articles = articles.filter(featured=True)
    return render(
        request,
        "learn/index.html",
        {
            "topics": _topics_with_counts(),
            "topic": topic,
            "articles": list(articles),
            "q": q,
            "nav_active": "learn",
        },
    )


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

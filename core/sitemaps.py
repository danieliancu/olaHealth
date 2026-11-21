"""sitemap.xml: public pages and the published, indexable Learn articles."""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from core.models import LearnArticle, LearnTopic


class LearnSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return LearnArticle.objects.filter(published=True, noindex=False, canonical_url="")

    def location(self, obj):
        return reverse("learn-article", args=[obj.slug])

    def lastmod(self, obj):
        return obj.updated_at


class PagesSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        return ["home", "learn", "products", "scan", "sources", "privacy", "terms"]

    def location(self, item):
        return reverse(item)


class TopicSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return LearnTopic.objects.all()

    def location(self, obj):
        return reverse("learn-topic", args=[obj.key])


SITEMAPS = {"learn": LearnSitemap, "topics": TopicSitemap, "pages": PagesSitemap}

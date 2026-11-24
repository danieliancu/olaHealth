"""Magazine passages for Ask Foodo: split, index and search published articles by meaning.

* ``passages_for(article)`` splits an article into passages: the title and summary first, then
  the body by its "## " / "### " sections, about ``TARGET_CHARS`` each (paragraphs are never cut).
* ``index_article(article)`` keeps ``ArticlePassage`` rows in step with the article: rebuilt when
  its text changes (``version``), deleted when it is unpublished. The full-text vector is
  written at once; the embedding (``ai.embeddings``) is added by ``embed_article`` in the worker.
  Saving or deleting an article does this through signals (``core.apps``) and bumps a cache
  generation, so no cached search can return an older or withdrawn passage.
* ``search(question)`` returns the best passages of published articles: by cosine similarity of
  embeddings when they can be used, else (or for passages not embedded yet) by PostgreSQL
  full-text rank. Every hit is re-read from the database, so an unpublished or deleted
  article is never returned.
* ``rebuild()`` (``manage.py rebuild_passages``) rebuilds everything from local content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Value

from core.models import ArticlePassage, LearnArticle

logger = logging.getLogger(__name__)

TARGET_CHARS = 700
MAX_CHARS = 1400
# Cosine similarity of text-embedding-3-small below which a passage is not relevant enough
# (unrelated texts score about 0.05-0.25; a passage answering the question 0.35 and more).
MIN_SIMILARITY = 0.32
MIN_RANK = 0.01  # full-text fallback (ts_rank_cd with OR of the question's words)
GEN_KEY = "passages:gen"
HITS_SECONDS = 3600
FTS_CONFIG = "english"

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPH = re.compile(r"\*{1,2}([^*]+)\*{1,2}")
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def plain(text: str) -> str:
    """Markup removed: links keep their text, bold and italic their words."""
    return _EMPH.sub(r"\1", _LINK.sub(r"\1", text or "")).strip()


def version_of(article: LearnArticle) -> str:
    raw = "\n".join((article.title, article.summary, article.body))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def passages_for(article: LearnArticle) -> list[tuple[str, str]]:
    """``(heading, text)`` pairs: the title and summary, then the body section by section."""
    out: list[tuple[str, str]] = [("", plain(f"{article.title}. {article.summary}"))]
    heading, buf = "", []

    def flush():
        if buf:
            out.append((heading, "\n".join(buf)))
            buf.clear()

    blocks = re.split(r"\n\s*\n", (article.body or "").strip().replace("\r\n", "\n"))
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith(("## ", "### ")):
            flush()
            heading = plain(lines[0].lstrip("#").strip())
            lines = lines[1:]
            if not lines:
                continue
        text = plain(" ".join(re.sub(r"^(- |\d+\.\s)", "", ln) for ln in lines))
        if buf and sum(len(b) for b in buf) + len(text) > TARGET_CHARS:
            flush()
        buf.append(text[:MAX_CHARS])
    flush()
    return [(h[:200], t) for h, t in out if t]


def bump_generation() -> None:
    try:
        cache.incr(GEN_KEY)
    except ValueError:
        cache.set(GEN_KEY, 2, None)


def generation() -> int:
    return cache.get_or_set(GEN_KEY, 1, None)


@transaction.atomic
def index_article(article: LearnArticle) -> bool:
    """Bring the article's passages in step with it. True when they were rebuilt (the
    embeddings are then missing until ``embed_article`` runs)."""
    if not article.published:
        deleted = ArticlePassage.objects.filter(article=article).delete()[0]
        return bool(deleted)
    version = version_of(article)
    current = ArticlePassage.objects.filter(article=article)
    if current.exists() and not current.exclude(version=version).exists():
        return False
    current.delete()
    ArticlePassage.objects.bulk_create(
        ArticlePassage(article=article, ordinal=i, heading=heading, text=text, version=version)
        for i, (heading, text) in enumerate(passages_for(article))
    )
    ArticlePassage.objects.filter(article=article).update(
        search=SearchVector(Value(article.title), weight="A", config=FTS_CONFIG)
        + SearchVector("heading", weight="A", config=FTS_CONFIG)
        + SearchVector("text", weight="B", config=FTS_CONFIG)
    )
    return True


def embed_article(article_id: int) -> int:
    """Add the missing embeddings of an article's passages (worker task). Returns how many."""
    from ai import embeddings

    rows = list(
        ArticlePassage.objects.filter(
            article_id=article_id, article__published=True, embedding__isnull=True
        )
    )
    if not rows:
        return 0
    vectors = embeddings.embed([f"{r.heading}\n{r.text}".strip() for r in rows])
    if not vectors:
        return 0
    for row, vector in zip(rows, vectors, strict=True):
        # Only if the passage is still the same text (the article may have changed meanwhile).
        ArticlePassage.objects.filter(pk=row.pk, version=row.version).update(
            embedding=vector, embedding_model=embeddings.MODEL
        )
    bump_generation()
    return len(rows)


def refresh_all() -> dict[str, int]:
    """Index every article from local content and add missing embeddings (schedule and
    ``manage.py rebuild_passages``). Unpublished and deleted articles lose their passages."""
    rebuilt = sum(index_article(a) for a in LearnArticle.objects.all())
    ArticlePassage.objects.exclude(article__published=True).delete()
    embedded = 0
    for article_id in (
        ArticlePassage.objects.filter(embedding__isnull=True)
        .values_list("article_id", flat=True)
        .distinct()
    ):
        embedded += embed_article(article_id)
    bump_generation()
    return {
        "articles": LearnArticle.objects.filter(published=True).count(),
        "rebuilt": rebuilt,
        "passages": ArticlePassage.objects.count(),
        "embedded": embedded,
        "without_embedding": ArticlePassage.objects.filter(embedding__isnull=True).count(),
    }


def rebuild() -> dict[str, int]:
    """Drop every passage and index again from the articles."""
    ArticlePassage.objects.all().delete()
    return refresh_all()


# ---------- Search ----------
@dataclass(frozen=True)
class Hit:
    passage: ArticlePassage
    score: float
    method: str  # "embedding" or "fulltext"

    @property
    def article(self) -> LearnArticle:
        return self.passage.article


def _fulltext(question: str, limit: int) -> list[tuple[int, float]]:
    words = sorted({w.lower() for w in _WORD.findall(question or "")})[:20]
    if not words:
        return []
    # OR of the words (stemmed by the "english" dictionary): a passage needs only some of them.
    query = SearchQuery(" | ".join(words), search_type="raw", config=FTS_CONFIG)
    rows = (
        ArticlePassage.objects.filter(article__published=True, search=query)
        .annotate(rank=SearchRank(F("search"), query, cover_density=True))
        .filter(rank__gte=MIN_RANK)
        .order_by("-rank")
        .values_list("pk", "rank")[:limit]
    )
    return [(pk, float(rank)) for pk, rank in rows]


def _by_embedding(vector: list[float], limit: int) -> list[tuple[int, float]]:
    from ai.embeddings import cosine

    rows = ArticlePassage.objects.filter(
        article__published=True, embedding__isnull=False
    ).values_list("pk", "embedding")
    scored = [(pk, cosine(vector, emb)) for pk, emb in rows]
    scored = [s for s in scored if s[1] >= MIN_SIMILARITY]
    return sorted(scored, key=lambda s: -s[1])[:limit]


def search(question: str, *, user=None, limit: int = 3) -> list[Hit]:
    """The best passages for ``question`` (at most one per article), from published articles
    only. Cached per cache generation; the hits are always re-read from the database."""
    from ai import embeddings

    question = (question or "").strip()
    if not question:
        return []
    key = f"passages:hits:{generation()}:" + hashlib.sha256(question.lower().encode()).hexdigest()
    found = cache.get(key)
    if found is None:
        vector = embeddings.embed_query(question, user=user)
        method = "embedding" if vector is not None else "fulltext"
        found = [
            (pk, score, method)
            for pk, score in (
                _by_embedding(vector, limit * 4) if vector else _fulltext(question, limit * 4)
            )
        ]
        cache.set(key, found, HITS_SECONDS)
    rows = ArticlePassage.objects.select_related("article", "article__topic").in_bulk(
        [pk for pk, _, _ in found]
    )
    hits, seen = [], set()
    for pk, score, method in found:
        row = rows.get(pk)
        if row is None or not row.article.published or row.article_id in seen:
            continue  # withdrawn or deleted since: never returned
        if row.version != version_of(row.article):
            continue  # the article changed since: its passages are being rebuilt
        seen.add(row.article_id)
        hits.append(Hit(row, round(score, 4), method))
        if len(hits) >= limit:
            break
    return hits

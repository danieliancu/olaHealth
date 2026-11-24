"""What product a chat question is about ("lapte de cocos", "coconut milk", "pește"), as
catalogue categories, in any of the app's languages.

1. The question's content words are kept (intent words, markets, nutrients, quantities and
   small words are dropped); Romanian food words are read through ``catalog.food_words``.
2. A category matches when one of its names or synonyms, in any language
   (``Category.names``), holds *all* the concept's words (singular or plural): "coconut milk"
   matches "Coconut milks and creams", never "Milks" (a generic tag alone is not enough) nor
   "Coconut waters". The most specific matching categories in use win (fewest extra words).
3. Products: those filed under the matched categories, plus products whose *name* (not the
   brand) holds all the concept's words; sold in the market asked for.

No food has a rule of its own: words and names are data.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from catalog.food_words import RO_EN

# Words that never name the product: asking, quantities, markets, nutrients, small words.
_STOP_WORDS = """
a an the of with and or for in on at to from by is are be there any some me my i you your
which what who whose how do does have has show find give get want looking recommend suggest
other another more less most least higher lower better best than products product items item
foods food buy available exist exists sold uk britain england romania france germany spain
italy usa us please per g ml kg l
ce care cine cum ai are au am sunt este există exista exist avem aveți aveti produse produs
produsele produsul alimente aliment alt alta altă alte altul niște niste un o unei unui de
din cu fără fara la în in pe pentru mi îmi imi mie îți iti te recomanzi recomandă recomanda
recomand recomandați sugerează sugereaza găsesc gasesc găsi gasi caut căutăm cautam vreau
dă-mi da-mi dami arată-mi arata-mi mai mult multă multa multe puțin putin puțină putina puține
putine decât decat cel cea cei cele care dintre ele acestea aceste acest aceasta regatul unit
marea britanie românia franța germania spania italia sua
zahăr zahar zaharuri sare sarea grăsimi grasimi grăsime grasime proteine fibre calorii
energie salt sugar sugars fat fats protein proteins fibre fiber energy calories kcal
saturated saturate foodo score scor
dar si și but also what about then atunci acum now ok
să sa fie aibă aiba aibe conțină contina conține contine ca cât cat câte cate azi today
quel quelle quels quelles est-ce qu y a a-t-il il-y-a sont des les le la du au aux avec sans et
qué que cuál cual cuáles hay los las el con sin para welche welcher welches gibt es ist mit
ohne für
"""
STOP = frozenset(_STOP_WORDS.split())


def fold(text: str) -> str:
    """Lower case, no accents."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _stem(word: str, english: bool = True) -> str:
    """A light singular: "milks" -> "milk", "fishes" -> "fish", "creams" -> "cream"; in other
    languages only a final "s" ("leches" -> "leche", "laits" -> "lait")."""
    rules = (("ies", "y"), ("shes", "sh"), ("ches", "ch"), ("oes", "o"), ("s", ""))
    for suffix, keep in rules if english else (("s", ""),):
        if len(word) > 3 and word.endswith(suffix):
            return word[: -len(suffix)] + keep
    return word


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\w-]+", fold(text)) if w]


def _name_tokens(name: str, english: bool = True) -> frozenset[str]:
    return frozenset(
        _stem(w, english) for w in re.split(r"[\s/,()&-]+", fold(name)) if w and w not in STOP
    )


def _head(english_name: str) -> str:
    """What an English category name is about: the last word before "with", "in", "and" or
    "for" ("Milk chocolates with almonds" -> "chocolate", "Coconut milks and creams" ->
    "milk", "Breakfast cereals" -> "cereal")."""
    main = re.split(r"\b(?:with|in|and|for|from|of)\b|[,(/&]", fold(english_name))[0]
    words = [w for w in re.split(r"[\s-]+", main) if w]
    return _stem(words[-1]) if words else ""


# Languages that put the main word first ("Yaourts au lait de coco" is about yogurt).
HEAD_FIRST = frozenset({"fr", "es", "it", "pt", "ro", "ca", "gl"})


def _head_of(name: str, lang: str) -> str:
    if lang == "en":
        return _head(name)
    words = [w for w in re.split(r"[\s/,()&-]+", fold(name)) if w and w not in STOP]
    if not words:
        return ""
    return _stem(words[0] if lang in HEAD_FIRST else words[-1], english=False)


_JOINED = re.compile(r"\b(and|et|und|y|e|și|si)\b|&", re.IGNORECASE)


@dataclass(frozen=True)
class Concept:
    words: tuple[str, ...]  # the concept in English words ("coconut", "milk")
    original: tuple[str, ...]  # the question's own words for it ("lapte", "cocos")
    tags: tuple[str, ...] = field(default=())  # matched categories, most specific first

    @property
    def label(self) -> str:
        return " ".join(self.original)


def concept_words(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(english words, original words)`` naming the product in a question."""
    from myola.services.rag import HIGHER_WORDS, LOWER_WORDS, MARKETS, NUTRIENTS

    drop = set(STOP)
    for words in list(MARKETS.values()) + [w for _, w in NUTRIENTS.values()]:
        drop.update(fold(w).strip() for w in words)
    # Comparison words ("low in", "sărac în", "fără") describe the criterion, not the product.
    for phrase in LOWER_WORDS + HIGHER_WORDS:
        drop.update(w for w in _words(phrase) if len(w) > 2)
    known = _vocabulary()
    original: list[str] = []
    english: list[str] = []
    for word in _words(text):
        if word in drop or word.isdigit() or len(word) < 2:
            continue
        en = RO_EN.get(word) or RO_EN.get(fold(word))
        # Only words the catalogue knows (a food word, or a word of a category name in any
        # language): "să aibă", "azi" or "mâncat" never become part of the product.
        if not en and _stem(word) not in known and _stem(word, english=False) not in known:
            continue
        original.append(word)
        english.append(_stem(fold(en or word)))
    # Keep each word once, in order.
    seen: set[str] = set()
    english = [w for w in english if not (w in seen or seen.add(w))]
    return tuple(english), tuple(original)


@lru_cache(maxsize=1)
def _index() -> tuple[tuple[str, int, tuple[tuple[frozenset[str], str, bool], ...]], ...]:
    """Categories used by listable products: ``(tag, products, names)``, each name as
    ``(tokens, head word or "" when not English, joined by "and", English)``."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT t, COUNT(*) FROM (SELECT unnest(categories) AS t FROM catalog_product "
            "WHERE listable AND current_revision_id IS NOT NULL) x GROUP BY t"
        )
        used = dict(cur.fetchall())
    from catalog.models import Category

    out = []
    for tag, name, names in Category.objects.values_list("tag", "name", "names"):
        entries = {(_name_tokens(name), _head(name), bool(_JOINED.search(name)), True)}
        for lang, items in (names or {}).items():
            for n in items:
                entries.add((_name_tokens(n, lang == "en"), _head_of(n, lang),
                             bool(_JOINED.search(n)), lang == "en"))  # fmt: skip
        out.append((tag, used.get(tag, 0), tuple(e for e in entries if e[0])))
    return tuple(out)


@lru_cache(maxsize=1)
def _family() -> tuple[dict[str, list[str]], dict[str, frozenset[str]], dict[str, int]]:
    """Parents, English name tokens and product counts of every category."""
    from catalog.models import Category

    parents, english = {}, {}
    for tag, name, ps in Category.objects.values_list("tag", "name", "parents"):
        parents[tag] = list(ps or [])
        english[tag] = _name_tokens(name)
    counts = {tag: count for tag, count, _ in _index()}
    return parents, english, counts


def _with_products(tag: str) -> str | None:
    """``tag`` when products use it; else the nearest parent that still names the same thing
    (its English name holds all of the child's words: "Coconut milks" -> "Coconut milks and
    creams", never "Coconut milks" -> "Plant-based milk alternatives")."""
    parents, english, counts = _family()
    seen, current = set(), tag
    while current and current not in seen:
        seen.add(current)
        if counts.get(current):
            return current
        child = english.get(current, frozenset())
        current = next(
            (p for p in parents.get(current, []) if child <= english.get(p, frozenset())), None
        )
    return None


@lru_cache(maxsize=1)
def _vocabulary() -> frozenset[str]:
    """Every word of every category name used by products, in every language."""
    words: set[str] = {_stem(w) for w in RO_EN.values()}  # food words, also in English
    for _, _, entries in _index():
        for tokens, *_ in entries:
            words.update(tokens)
    return frozenset(words)


def reset_index() -> None:
    _vocabulary.cache_clear()
    _index.cache_clear()
    _family.cache_clear()


def resolve(text: str) -> Concept | None:
    """The concept a question names, with its most specific categories (None: no product
    words in the question)."""
    english, original = concept_words(text)
    if not english:
        return None
    want_en = frozenset(english)
    want_own = frozenset(_stem(w, english=False) for w in original)
    best: list[tuple[float, int, str]] = []
    for tag, count, entries in _index():
        scores = []
        for tokens, head, joined, is_en in entries:
            # English words against English names (about the same thing: their head word is
            # one of the concept's), the question's own words against other languages' names.
            if is_en and want_en <= tokens and head in want_en:
                extra = len(tokens - want_en)
            elif not is_en and want_own and want_own <= tokens and head in want_own:
                extra = len(tokens - want_own)
            else:
                continue
            scores.append(extra + (0.5 if joined else 0))
        if scores:
            best.append((min(scores), -count, tag))
    # The most specific match (fewest extra words; a name joining two things with "and" is
    # less specific); among equals, the category with the most products.
    tags: tuple[str, ...] = ()
    for _, _, tag in sorted(best):
        found = _with_products(tag)
        if found:
            tags = (found,)
            break
    return Concept(words=english, original=original, tags=tags)


def products(concept: Concept, markets: list[str]):
    """Listable products of the concept sold in ``markets``: filed under its categories, or
    named with all its words (the name, not the brand)."""
    from django.db.models import Q

    from catalog.models import Product

    match = Q(categories__overlap=list(concept.tags)) if concept.tags else Q(pk__in=[])
    # By name only for a concept of several words ("coconut milk"): one word ("oil") in a
    # name says little ("Sardines in sunflower oil").
    by_name = Q(pk__in=[])
    if len(concept.words) > 1:
        by_name = Q()
        for word in concept.words:
            by_name &= Q(name__iregex=rf"\m{re.escape(word)}")
    found = Product.objects.filter(current_revision__isnull=False, listable=True).filter(
        match | by_name
    )
    if markets:
        found = found.filter(countries__overlap=markets)
    return found

"""Ask Foodo with retrieval: product comparisons from the catalogue, explanations from the
Magazine.

Two kinds of material, both chosen by code before the AI provider sees anything:

* **Products** (``compare_products``). A question such as "Which UK pizza has less salt than
  this one?" becomes a ``Criterion`` (a nutrient and a direction, or the Foodo Score), a
  category (the scanned product's, or one named in the question) and a market (named in the
  question, else the user's). Candidates are read from PostgreSQL (published, listable products
  of that category sold in that market), with their *current* values (``current_revision``:
  approved corrections included). Values are compared in code, with ``Decimal``, only on the
  same nutrient, unit, per-100 basis (g or ml) and preparation (as sold), declared values only.
  A missing value is unknown: never 0, never "meets the criterion". "Less salt" and "a higher
  Foodo Score" are two different criteria, never mixed.
* **Magazine passages** (``core.passages.search``): published articles only, found by meaning.

The model gets only that material (``ai.service.ask(mode="rag")``), with short ids ([P1],
[A1]) to cite. Links are built here from the products and articles actually selected, never
taken from the model's text; ids the model invents are removed. When the AI cannot answer (off,
limits, failure), the same material is phrased by ``deterministic_answer``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db.models import F
from django.urls import reverse

from catalog.models import Category, NutrientFact, Product
from catalog.nutrition import BASIS_LABELS
from catalog.presenters import format_value

AS_SOLD = "as_sold"
PER_100 = ("per_100g", "per_100ml")
MAX_MATCHES = 3
MAX_CANDIDATES = 2000
MAX_PASSAGES = 2

# nutrient key -> (English name, words in the app's languages)
NUTRIENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "salt": ("salt", ("salt", "sare", "sarea", "sodium", "sodiu", "sel", "sal ", "salz")),
    "sugars": ("sugar", ("sugar", "zahăr", "zahar", "zaharu", "sucre", "azúcar", "zucchero",
                          "zucker", "cukier")),
    "saturated-fat": ("saturated fat", ("saturated", "saturates", "saturat", "saturées",
                                        "gesättigt")),
    "fat": ("fat", ("fat ", "fats", "grăsim", "grasim", "lipid", "graisse", "grasa", "fett")),
    "energy-kcal": ("energy (kcal)", ("calor", "kcal", "energy", "energie", "énergie")),
    "fiber": ("fibre", ("fibre", "fiber", "fibr")),
    "proteins": ("protein", ("protein", "proteín", "protéin")),
}  # fmt: skip
FOODO_WORDS = ("foodo", "score", "scor", "punctaj", "nutri")
LOWER_WORDS = (
    "less", "lower", "fewer", "least", "lowest", "low ", "reduced", "mai puțin", "mai putin",
    "mai puţin", "mai mic", "mai redus", "mai scăzut", "mai scazut", "cel mai mic", "moins",
    "menos", "weniger", "meno", "mniej",
    # "sărac în grăsimi", "cu puțin zahăr", "low in salt", "fără zahăr"
    "sărac", "sarac", "săracă", "saraca", "sărace", "sarace", "cu puțin", "cu putin",
    "cu puțină", "cu putina", "cu puține", "cu putine", "low in", "low-", " fără ", " fara ",
    "without", "pauvre en", "bajo en",
)  # fmt: skip
HIGHER_WORDS = (
    "more", "higher", "highest", "most", "better", "best", "mai mult", "mai mare", "mai bun",
    "cel mai mare", "plus de", "más", "mehr", "più", "więcej", "mai ridicat",
)  # fmt: skip
COMPARE_WORDS = (
    "than", "decât", "decat", "compared", "comparison", "față de", "fata de", "que ", "als ",
    "rispetto", "niż", "instead", "alternative", "alternativ", "în loc", "in loc", "vs",
    "which", "what", "ce ", "care", "quel", "cuál", "welche", "quale", "swap", "înlocui",
    "recommend", "recomand", "suggest", "suger", "another", "other", " alt ", " alta ",
    " altă ", " alte ", "autre", "otro", "andere",
    # "pot să găsesc…", "dă-mi un produs…", "show me…", "¿qué…?" ask for a product too.
    "găsesc", "gasesc", "caut", "find", "looking for", "dă-mi", "da-mi", "dami", "give me",
    "arată-mi", "arata-mi", "show me", "vreau", "i want", "qué", "quelle", "welcher",
    "welches", "qual", "jaki", "jaka",
)  # fmt: skip
MARKETS: dict[str, tuple[str, ...]] = {
    "en:united-kingdom": ("uk", "u.k.", "united kingdom", "britain", "british", "england",
                          "regatul unit", "marea britanie", "britanic", "royaume-uni",
                          "reino unido", "großbritannien"),
    "en:romania": ("romania", "românia", "romanian", "românesc", "roumanie", "rumänien"),
    "en:france": ("france", "franța", "franta", "french"),
    "en:germany": ("germany", "germania", "german", "deutschland", "allemagne"),
    "en:spain": ("spain", "spania", "españa", "espagne"),
    "en:italy": ("italy", "italia", "italie"),
}  # fmt: skip
_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_CITE = re.compile(r"\[(P\d+|A\d+)\]")


@dataclass(frozen=True)
class Criterion:
    nutrient: str  # a NUTRIENTS key, or "foodo"
    direction: str  # "lower" or "higher"

    @property
    def label(self) -> str:
        if self.nutrient == "foodo":
            return "a higher Foodo Score"
        return f"{'less' if self.direction == 'lower' else 'more'} {NUTRIENTS[self.nutrient][0]}"


@dataclass
class Match:
    product: Product
    value: Decimal | None
    unit: str
    foodo: int | None
    difference: Decimal | None = None


@dataclass
class Comparison:
    criterion: Criterion
    markets: list[str]  # OFF country tags the candidates must be sold in (any of them)
    category: str
    category_name: str
    reference: Product | None
    reference_value: Decimal | None
    basis: str
    unit: str
    reference_foodo: int | None
    matches: list[Match] = field(default_factory=list)
    candidates: int = 0  # products of that category and market (the reference excluded)
    unknown: int = 0  # candidates without the value on the same basis (not compared)
    problem: str = ""  # why there is no comparison: "no_reference", "no_reference_value", …
    market_source: str = ""  # "question", "product" (where it is sold), "user" or "" (none)
    # Why each candidate was left out (diagnostics, kept with the answer, never shown):
    # avoid, no_value, other_unit, label_bound ("< x" / "> x"), not_better, other_scale.
    excluded: dict[str, int] = field(default_factory=dict)

    def exclude(self, reason: str) -> None:
        self.excluded[reason] = self.excluded.get(reason, 0) + 1

    def details(self) -> dict[str, Any]:
        """What the answer was built from (``ChatMessage.details``; follow-ups read it)."""
        return {
            "kind": "comparison",
            "criterion": {
                "nutrient": self.criterion.nutrient,
                "direction": self.criterion.direction,
            },
            "markets": self.markets,
            "market_source": self.market_source,
            "category": self.category,
            "basis": self.basis,
            "unit": self.unit,
            "candidates": self.candidates,
            "compared": self.candidates - self.unknown,
            "excluded": self.excluded,
            "problem": self.problem,
            "products": (
                [_product_detail(self.reference, self.reference_value, self.reference_foodo)]
                if self.reference is not None
                else []
            )
            + [_product_detail(m.product, m.value, m.foodo) for m in self.matches],
        }


def _product_detail(p: Product, value: Decimal | None, foodo: int | None) -> dict[str, Any]:
    return {
        "id": str(p.pk),
        "name": p.name or "",
        "brand": p.brand or "",
        "value": str(value) if value is not None else None,
        "foodo": foodo,
    }


# ---------- Reading the question ----------
_PUNCT = re.compile(r"[?!.,;:¿¡()\"“”„«»]")


def _norm(text: str | None) -> str:
    """The question as the word lists read it: lower case, punctuation as spaces, padded."""
    return f" {_PUNCT.sub(' ', (text or '').lower())} "


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def criterion_of(text: str) -> Criterion | None:
    """The one criterion of a comparison question, or None when it is not one. "Less salt"
    and "a higher Foodo Score" are different criteria: a question naming a nutrient compares
    that nutrient; the Foodo Score only when it names the score and no nutrient."""
    t = _norm(text)
    lower, higher = _has(t, LOWER_WORDS), _has(t, HIGHER_WORDS)
    if not (lower or higher) or not _has(t, COMPARE_WORDS):
        return None
    for key, (_, words) in NUTRIENTS.items():
        if key == "fat" and _has(t, NUTRIENTS["saturated-fat"][1]):
            continue
        if _has(t, words):
            return Criterion(key, "lower" if lower else "higher")
    if _has(t, FOODO_WORDS):
        return Criterion("foodo", "higher")
    return None


def market_of(text: str) -> str | None:
    t = _norm(text)
    for tag, words in MARKETS.items():
        if any(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", t) for w in words):
            return tag
    return None


def category_of(text: str, product: Product | None) -> str | None:
    """The category to compare in: one of the scanned product's categories named in the
    question (the deepest), else its reference category; without a product, a category tag
    named in the question that has products."""
    from catalog.services.alternatives import reference_categories

    words = {w.lower() for w in _WORD.findall(text or "")}
    stems = {w.rstrip("s") for w in words}

    def named(tag: str) -> bool:
        name = tag.split(":", 1)[-1].replace("-", " ")
        return name.rstrip("s") in stems or name in words

    if product is not None:
        tags = [t for t in (product.categories or []) if named(t)]
        if tags:
            return tags[-1]
        ref = reference_categories(product)
        return ref[0] if ref else None
    for stem in sorted(stems, key=len, reverse=True):
        for tag in (f"en:{stem}s", f"en:{stem}", f"en:{stem}es"):
            if Product.objects.filter(categories__contains=[tag], listable=True).exists():
                return tag
    return None


def category_name(tag: str) -> str:
    row = Category.objects.filter(tag=tag).values_list("name", flat=True).first()
    return row or tag.split(":", 1)[-1].replace("-", " ").capitalize()


# ---------- Values (current revision, same basis and preparation) ----------
def _values(revision_ids: list, nutrient: str, basis: str | None) -> dict[Any, tuple]:
    """``{revision_id: (value, unit, basis)}``: declared, as sold, per 100 g or ml."""
    rows = NutrientFact.objects.filter(
        revision_id__in=revision_ids,
        nutrient=nutrient,
        preparation=AS_SOLD,
        status="declared",
        value__isnull=False,
        basis__in=[basis] if basis else PER_100,
    ).values_list("revision_id", "value", "unit", "basis", "modifier")
    out: dict[Any, tuple] = {}
    for rid, value, unit, b, modifier in rows:
        if rid not in out or b == "per_100g":  # one basis per product (g first)
            out[rid] = (value, unit, b, modifier or "")
    return out


def _meets(value: Decimal, modifier: str, ref: Decimal, direction: str) -> bool | None:
    """Whether a declared value meets the criterion against the reference; None when the
    label does not allow to say (for example "> 2 g" for "less than 1.5 g")."""
    if modifier in ("", "~"):
        return value < ref if direction == "lower" else value > ref
    if modifier in ("<", "<="):  # the real value is below ``value``
        return True if direction == "lower" and value <= ref else None
    if modifier in (">", ">="):
        return True if direction == "higher" and value >= ref else None
    return None


def _foodo(revision) -> tuple[int | None, dict]:
    from assessments.score import foodo_value, score_for_revision

    score = score_for_revision(revision)
    return foodo_value(score), score


def markets_for(text: str, product: Product | None, user=None) -> tuple[list[str], str]:
    """Where the candidates must be sold, and why: the market named in the question; else
    where the scanned product is sold (the user's market first when it is one of them); else
    the user's market. A product sold elsewhere is never compared with products of another
    market by default."""
    from myola.context.django_source import user_country

    named = market_of(text)
    if named:
        return [named], "question"
    mine = user_country(user)
    if product is not None and product.countries:
        if mine in product.countries:
            return [mine], "product"
        return list(product.countries), "product"
    if product is not None:
        return [], ""  # the source does not say where it is sold: no market filter
    return [mine], "user"


def compare_products(text: str, product: Product | None, user=None) -> Comparison | None:
    """The comparison asked for, computed from the database (None: not a comparison)."""
    from catalog.services.alternatives import _user_filters

    criterion = criterion_of(text)
    if criterion is None:
        return None
    markets, source = markets_for(text, product, user)
    category = category_of(text, product)
    if category is None:
        return None if product is None else Comparison(
            criterion, markets, "", "", product, None, "", "", None, problem="no_category",
            market_source=source,
        )  # fmt: skip
    result = Comparison(
        criterion, markets, category, category_name(category), product, None, "", "", None,
        market_source=source,
    )  # fmt: skip
    if product is None:
        result.problem = "no_reference"
        return result
    ref_rev = product.current_revision
    if criterion.nutrient == "foodo":
        result.reference_foodo, ref_score = _foodo(ref_rev)
        if result.reference_foodo is None:
            result.problem = "no_reference_value"
            return result
    else:
        ref = _values([ref_rev.pk], criterion.nutrient, None).get(ref_rev.pk)
        if ref is None or ref[3] not in ("", "~"):  # unknown, or only "< x" / "> x"
            result.problem = "no_reference_value"
            return result
        result.reference_value, result.unit, result.basis, _ = ref
        result.reference_foodo = _foodo(ref_rev)[0]

    _, avoid_ids, _ = _user_filters(user)
    found = Product.objects.filter(
        current_revision__isnull=False, listable=True, categories__contains=[category]
    )
    if markets:
        found = found.filter(countries__overlap=markets)
    candidates = list(
        found.exclude(pk=product.pk)
        .select_related("current_revision")
        .order_by("pk")[:MAX_CANDIDATES]
    )
    kept = [p for p in candidates if str(p.pk) not in avoid_ids]
    for _ in range(len(candidates) - len(kept)):
        result.exclude("avoid")
    candidates = kept
    result.candidates = len(candidates)
    matches: list[Match] = []
    if criterion.nutrient == "foodo":
        from assessments.score import _comparable, is_better

        for p in candidates:
            value, score = _foodo(p.current_revision)
            if value is None:
                result.unknown += 1
                result.exclude("no_value")
            elif not _comparable(score, ref_score):
                result.unknown += 1
                result.exclude("other_scale")  # another Nutri-Score version or category
            elif not is_better(score, ref_score):
                result.exclude("not_better")
            else:
                matches.append(Match(p, None, "", value, Decimal(value - result.reference_foodo)))
        matches.sort(key=lambda m: (-(m.foodo or 0), m.product.name))
    else:
        values = _values(
            [p.current_revision_id for p in candidates], criterion.nutrient, result.basis
        )
        for p in candidates:
            row = values.get(p.current_revision_id)
            if row is None or row[1] != result.unit:
                result.unknown += 1  # no declared value on the same basis and unit
                result.exclude("no_value" if row is None else "other_unit")
                continue
            value = row[0]
            better = _meets(value, row[3], result.reference_value, criterion.direction)
            if better is None:
                result.unknown += 1  # "< x" / "> x" that cannot be compared
                result.exclude("label_bound")
                continue
            if better:
                matches.append(Match(p, value, row[1], None, value - result.reference_value))
            else:
                result.exclude("not_better")
        matches.sort(
            key=lambda m: (m.value if criterion.direction == "lower" else -m.value, m.product.name)
        )
        matches = matches[:MAX_MATCHES]
        for m in matches:
            m.foodo = _foodo(m.product.current_revision)[0]
    result.matches = matches[:MAX_MATCHES]
    return result


# ---------- Payload, sources, answers ----------
def _amount(value: Decimal | None, unit: str) -> str:
    return format_value(value, unit) if value is not None else "unknown"


def _product_url(p: Product) -> str:
    return reverse("product", args=[p.pk])


def _name(p: Product) -> str:
    name = p.name or "Unnamed product"
    return f"{name} ({p.brand})" if p.brand else name


def product_card(
    p: Product,
    lang: str = "en",
    nutrient: str | None = None,
    value=None,
    unit: str = "g",
    basis: str = "per_100g",
    foodo: int | None = None,
) -> dict:
    """A product as a source card under an answer: its name, then the brand and the value
    the question was about (a verified value from the product record), and its photo."""
    from myola.services import chat_text as W

    second = [p.brand] if p.brand else []
    if nutrient == "foodo" and foodo is not None:
        second.append(f"Foodo Score {foodo}/100")
    elif nutrient and value is not None:
        second.append(W.card_value(lang, nutrient, value, unit or "g", basis or "per_100g"))
    return {
        "kind": "product",
        "title": p.name or W.t(lang, "unnamed"),
        "url": _product_url(p),
        "subtitle": " · ".join(second),
        "image": p.front_image_url or "",
    }


def article_card(article, lang: str = "en") -> dict:
    """A Magazine article as a source card: its title, then its topic and reading time, and
    its cover photo."""
    from myola.services import chat_text as W

    second = [article.topic.title] if article.topic_id else []
    if article.minutes:
        second.append(W.t(lang, "read_time", n=article.minutes))
    return {
        "kind": "article",
        "title": article.title,
        "url": reverse("learn-article", args=[article.slug]),
        "subtitle": " · ".join(second),
        "image": article.image_url(),
    }


@dataclass
class Material:
    """What the model may use, with the ids it may cite, and the links behind those ids."""

    comparison: Comparison | None
    hits: list
    facts: dict[str, Any]
    sources: dict[str, dict[str, str]]


def build_material(
    comparison: Comparison | None, hits: list, product_facts=None, lang: str = "en"
) -> Material:
    facts: dict[str, Any] = {}
    sources: dict[str, dict[str, str]] = {}
    if comparison is not None:
        c = comparison
        basis = BASIS_LABELS.get(c.basis, c.basis) if c.basis else ""
        block: dict[str, Any] = {
            "criterion": c.criterion.label,
            "market": markets_label(c.markets) or "any market",
            "category": c.category_name,
            "compared_on": f"{basis}, as sold, declared values" if basis else "Foodo Score",
            "products_in_category_and_market": c.candidates,
            "without_the_value_not_compared": c.unknown,
        }
        if c.reference is not None:
            sources["P0"] = product_card(
                c.reference, lang, c.criterion.nutrient, c.reference_value, c.unit, c.basis,
                c.reference_foodo,
            )  # fmt: skip
            block["scanned_product"] = {
                "id": "P0",
                "name": _name(c.reference),
                "value": _amount(c.reference_value, c.unit) if c.basis else None,
                "foodo_score": (
                    f"{c.reference_foodo}/100" if c.reference_foodo is not None else None
                ),
            }
        if c.problem:
            block["problem"] = {
                "no_reference": "no scanned product to compare with",
                "no_reference_value": "the scanned product's value is not known",
                "no_category": "the product's category is not known",
            }[c.problem]
        block["matches"] = []
        for i, m in enumerate(c.matches, start=1):
            pid = f"P{i}"
            sources[pid] = product_card(
                m.product, lang, c.criterion.nutrient, m.value, m.unit, c.basis, m.foodo
            )
            block["matches"].append(
                {
                    "id": pid,
                    "name": _name(m.product),
                    "value": _amount(m.value, m.unit) if c.basis else None,
                    "difference": (_amount(m.difference, m.unit) if c.basis
                                   else f"{m.difference:+} points"),
                    "foodo_score": f"{m.foodo}/100" if m.foodo is not None else None,
                }
            )  # fmt: skip
        facts["comparison"] = block
    if hits:
        facts["magazine"] = []
        for i, hit in enumerate(hits[:MAX_PASSAGES], start=1):
            aid = f"A{i}"
            sources[aid] = article_card(hit.article, lang)
            facts["magazine"].append(
                {
                    "id": aid,
                    "article": hit.article.title,
                    "section": hit.passage.heading,
                    "passage": hit.passage.text,
                }
            )
    if product_facts:
        facts["product"] = product_facts
    return Material(comparison, hits, facts, sources)


def cited_sources(text: str, material: Material) -> tuple[str, list[dict[str, str]]]:
    """The visible answer and its sources. The ids ([P1], [A1]) only tell which material the
    answer used: they are removed from the text, and the sources are linked under it instead:
    the products of the comparison always (they are its result), articles only when cited.
    Ids that are not in the material are dropped."""
    used = {m.group(1) for m in _CITE.finditer(text) if m.group(1) in material.sources}
    clean = _CITE.sub("", text)
    clean = re.sub(r"[ \t]+([.,;:!?)])", r"\1", clean)  # no space left before punctuation
    clean = re.sub(r"\(\s*\)|,\s*(?=[.;])", "", clean)  # "( )" or ", ." left by an id
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    clean = "\n".join(line.rstrip() for line in clean.strip().splitlines())
    out = [s for k, s in material.sources.items() if k.startswith("P")]
    out += [s for k, s in material.sources.items() if k.startswith("A") and k in used]
    return clean, out


def markets_label(markets: list[str]) -> str:
    """ "the UK", "Belgium or the Netherlands" ("" without a market filter)."""
    from myola.services.market import name

    names = [str(name(tag)) for tag in markets]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def has_answer(comparison: Comparison | None) -> bool:
    """Whether the comparison found products to talk about (else the short direct answer)."""
    return comparison is not None and not comparison.problem and bool(comparison.matches)


def direct_answer(c: Comparison, lang: str = "en") -> str:
    """The comparison's answer, written from the data only (every value is the database's):
    the products that meet the criterion with their values, or why there are none. It names
    the category and the market applied, and how many products could be compared. In English
    or Romanian (``rag_text``)."""
    from myola.services import chat_text as W

    foodo = c.criterion.nutrient == "foodo"
    what = W.nutrient(lang, c.criterion.nutrient)
    label = W.criterion(lang, c.criterion.nutrient, c.criterion.direction)
    scope = (
        W.t(lang, "scope", category=c.category_name) if c.category_name else W.t(lang, "scope_any")
    )
    if c.markets:
        scope += W.t(lang, "sold_in", markets=W.markets(c.markets, lang))
    if c.problem == "no_reference":
        return W.t(lang, "open_first")
    if c.problem == "no_category":
        return W.t(lang, "no_category")
    if c.problem == "no_reference_value":
        return W.t(lang, "no_foodo") if foodo else W.t(lang, "no_value", what=what)
    ref = (
        f"{W.number(c.reference_value, c.unit, lang)} {W.basis(lang, c.basis)}"
        if c.basis
        else f"{c.reference_foodo}/100"
    )
    if c.candidates == 0:
        return W.t(lang, "nothing_to_compare", scope=scope)
    compared = c.candidates - c.unknown
    if compared == 0:
        return W.t(lang, "not_enough", n=c.candidates, scope=scope, what=what, label=label)
    tally = W.t(lang, "tally_one" if c.candidates == 1 else "tally", compared=compared,
                n=c.candidates)  # fmt: skip
    if c.unknown:
        key = "tally_unknown_one" if c.unknown == 1 else "tally_unknown"
        tally += W.t(lang, key, unknown=c.unknown, what=what)
    if not c.matches:
        return W.t(lang, "no_match", scope=scope, label=label, ref=ref, tally=tally)
    lines = [W.t(lang, "with", label=label, ref=ref, scope=scope)]
    for m in c.matches:
        value = W.number(m.value, m.unit, lang) if c.basis else f"{m.foodo}/100"
        lines.append(f"- {_name(m.product)}: {value}")
    lines.append(f"{tally}.")
    return "\n".join(lines)


_EXPLAIN = re.compile(
    r"\b(how|why|explain|cum|de ce|explic|comment|pourquoi|wie|warum|cómo|por qué|come|perché|"
    r"jak|dlaczego)\b",
    re.IGNORECASE,
)


def asks_explanation(text: str) -> bool:
    """The question also asks how or why (for example "and how do I compare the labels?")."""
    return bool(_EXPLAIN.search(text or ""))


# ---------- A value of the open product ----------
VALUE_CUES = (
    "cât", "câtă", "câte", " cat ", " cata ", " cate ", "how much", "how many", "what is the",
    "care e ", "care este", "conține", "contine", "is in it", "does it have", "has it",
    "valoare", "value", "per 100", "la 100", "quantity", "cantitate",
)  # fmt: skip


def nutrient_asked(text: str) -> str | None:
    """The one nutrient a question about the open product names ("Câtă sare are?", "How much
    sugar is in it?"), when it is not a comparison. None otherwise."""
    t = _norm(text)
    if criterion_of(text) is not None or concept_of(text) or not _has(t, VALUE_CUES):
        return None  # a comparison, a thing the catalogue does not record, or not "how much"
    for key, (_, words) in NUTRIENTS.items():
        if key == "fat" and _has(t, NUTRIENTS["saturated-fat"][1]):
            continue
        if _has(t, words):
            return key
    return None


def value_answer(product: Product, nutrient: str, lang: str = "en") -> tuple[str, dict[str, Any]]:
    """The product's declared value per 100 g or 100 ml (as sold), from its current revision.
    0 is a value; a missing value is said to be missing (never 0)."""
    from myola.services import chat_text as W

    name = W.nutrient(lang, nutrient)
    row = _values([product.current_revision_id], nutrient, None).get(product.current_revision_id)
    details: dict[str, Any] = {
        "kind": "value", "nutrient": nutrient, "basis": "", "unit": "",
        "products": [_product_detail(product, None, None)],
    }  # fmt: skip
    if row is None:
        serving = NutrientFact.objects.filter(
            revision_id=product.current_revision_id, nutrient=nutrient, basis="per_serving",
            preparation=AS_SOLD, status="declared", value__isnull=False,
        ).values_list("value", "unit").first()  # fmt: skip
        if serving:
            shown = W.number(serving[0], serving[1], lang)
            return W.t(lang, "value_serving", what=name, value=shown), details
        return W.t(lang, "value_none", what=name), details
    value, unit, basis, modifier = row
    details.update(basis=basis, unit=unit)
    details["products"] = [_product_detail(product, value, None)]
    shown = W.number(value, unit, lang)
    if modifier in ("<", ">"):
        shown = f"{modifier}{shown}"
    text = W.t(lang, "value", What=W.capital(name), value=shown, basis=W.basis(lang, basis))
    return text, details


# ---------- Requests on the shared catalogue ----------
# One reading of a question about products of the catalogue (``interpret``), used for the
# database query, the answer and the cards, and kept on the answer (``details["request"]``).
# Things people ask for that the catalogue does not record as a nutrient. ``off_key``: the OFF
# field read from the stored OFF data when a product has it; ``category``: where OFF files the
# foods it applies to ("fatty fishes" for omega-3 in fish), used only when the value is missing.
CONCEPTS: dict[str, dict[str, Any]] = {
    "omega-3": {
        "words": ("omega-3", "omega 3", "omega3", "omega‑3", "omega–3"),
        "off_key": "omega-3-fat_100g",
        "category": {"en:fishes": "en:fatty-fishes"},
    },
}
RECOMMEND_WORDS = (
    "recoman", "recommend", "suggest", "suger", "unde găsesc", "unde gasesc", "unde pot găsi",
    "unde pot gasi", "where can i find", "where do i find", "where can i get", "ce produs are",
    "ce produse au", "what product has", "which product has", "which products have",
    # "poți să-mi arăți…", "arată-mi…", "dă-mi…", "vreau…", "show me…", "I want…"
    "arăt", "arat", "dă-mi", "da-mi", "dami", "vreau", "am nevoie", "show me", "give me",
    "i want", "i need", "montre", "muéstrame", "zeig",
)  # fmt: skip
# Asking what the catalogue holds ("Ce produse … există?", "Which … are sold in the UK?").
DISCOVER_WORDS = (
    " există", " exista", " exist", " existe ", "are there", "is there", " available",
    " sold", " sell ", " listed", "do you have", " aveți", " aveti", " hay ", "gibt es",
    "y a-t-il", "il y a", " găsesc", " gasesc", " caut", " find",
)  # fmt: skip
ASK_WORDS = (" ce ", " care ", " which ", " what ", " quel", " qué ", " que ", " welche",
             " quale", " qual ", " jaki", " jaka")  # fmt: skip
ANAPHORA = (
    "aceste", "acestea", "astea", "acelea", "aceleași", "aceleasi", "acesta", "these", "those",
    "them", "this fat", "such",
)  # fmt: skip
THIS_PRODUCT = ("scanat", "scanned", "this one", "this product", "acest produs", "acesta",
                "decât produsul", "decat produsul", "than this")  # fmt: skip
# "Care dintre ele…?", "Which of them…?": the products of the previous answer.
GROUP_WORDS = (
    "dintre ele", "dintre acestea", "dintre astea", "dintre ei", "dintre cele", "care dintre",
    "which of them", "which one of them", "which of these", "which of those", "among them",
    "of these", "of those", "between them", "lequel", "laquelle", "cuál de ellos",
)  # fmt: skip
LIST_LIMIT = 8


def concept_of(text: str) -> str | None:
    t = (text or "").lower()
    for key, concept in CONCEPTS.items():
        if _has(t, concept["words"]):
            return key
    return None


def nutrient_in(text: str) -> str | None:
    """The nutrient this message names (saturated fat before fat), or None."""
    t = _norm(text)
    for key, (_, words) in NUTRIENTS.items():
        if key == "fat" and _has(t, NUTRIENTS["saturated-fat"][1]):
            continue
        if _has(t, words):
            return key
    return None


def subject_of(text: str, earlier: list[str]) -> tuple[str | None, str]:
    """What the question asks the products to have: a nutrient key or a concept ("omega-3"),
    and where it came from. Only a reference ("aceste grăsimi", "these fats") reads the user's
    earlier messages; a new question never inherits an earlier criterion."""
    t = _norm(text)
    own = concept_of(text)
    if own:
        return own, "question"
    named = nutrient_in(text)
    if _has(t, ANAPHORA):
        # "Aceste grăsimi" after a message about omega-3: the thing the catalogue does not
        # record; otherwise the nutrient this message names wins over earlier ones.
        for message in earlier:
            found = concept_of(message) or (None if named else nutrient_in(message))
            if found:
                return found, "conversation"
    return (named, "question") if named else (None, "")


@dataclass
class Request:
    intent: str  # discover | recommend | group | clarify
    concept: dict[str, Any] = field(default_factory=dict)  # words, original, tags
    market: str = ""
    market_source: str = ""  # question | conversation | user
    subject: str | None = None  # a nutrient key or a concept key ("omega-3")
    subject_origin: str = ""
    direction: str = "higher"
    scope: list[str] = field(default_factory=list)  # product ids ("dintre ele")
    inherited: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent, "concept": self.concept, "market": self.market,
            "market_source": self.market_source, "subject": self.subject,
            "subject_origin": self.subject_origin, "direction": self.direction,
            "scope": self.scope, "inherited": self.inherited,
        }  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Request:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def interpret(text: str, previous: dict | None, product: Product | None, user=None,
              earlier: list[str] | None = None) -> Request | None:  # fmt: skip
    """The request a chat message makes on the shared catalogue, or None (another kind of
    question: the user's own data, a comparison with the open product, the Magazine...).

    * a complete question naming a kind of product starts a new search: its own product, its
      own criterion (only from this message or a reference such as "these fats"), the market
      it names, else the app's policy (the user's market);
    * "care dintre ele / which of them" compares the products of the previous answer;
    * "dar în România? / and in Romania?" repeats the previous search in another market;
    * a reference with nothing to refer to asks one short question back."""
    from catalog.services import concepts
    from myola.context.django_source import user_country

    earlier = earlier or []
    t = _norm(text)
    previous = previous or {}
    before = previous.get("request") or {}
    direction = "lower" if _has(t, LOWER_WORDS) else "higher"
    nutrient = nutrient_in(text)
    if _has(t, GROUP_WORDS):
        ids = [p["id"] for p in previous.get("products") or [] if p.get("id")]
        if not ids or not nutrient:
            return Request("clarify", subject=nutrient, inherited=["products"] if ids else [])
        return Request("group", concept=before.get("concept") or {}, scope=ids,
                       subject=nutrient, subject_origin="question", direction=direction,
                       market=before.get("market", ""), market_source="conversation",
                       inherited=["products"])  # fmt: skip
    market = market_of(text)
    concept = concepts.resolve(text)
    # A question about products: asking for them ("recomandă", "există", "find") or with a
    # question word ("ce", "which").
    asks = _has(t, RECOMMEND_WORDS + DISCOVER_WORDS + ASK_WORDS)
    named = concept is not None and bool(concept.tags)
    unknown = concept is not None and not concept.tags and _has(t, RECOMMEND_WORDS + DISCOVER_WORDS)
    if market and not named and not unknown and not nutrient and before.get("concept"):
        again = Request.from_dict(before)
        again.market, again.market_source = market, "question"
        again.inherited = ["concept", "subject"]
        if again.intent in ("group", "clarify"):  # the search the group came from
            again.intent, again.scope = ("recommend" if again.subject else "discover"), []
        return again
    if not ((named and asks) or unknown):
        return None
    same_kind = product is not None and named and set(concept.tags) & set(product.categories or [])
    if same_kind and (criterion_of(text) is not None or _has(t, THIS_PRODUCT)):
        return None  # a comparison with the open product
    if _has(t, THIS_PRODUCT) and product is None:
        return None  # "than the scanned product": open a product first
    subject, origin = subject_of(text, earlier)
    recommend_words = _has(t, RECOMMEND_WORDS)
    return Request(
        intent="recommend" if subject or recommend_words else "discover",
        concept={"words": list(concept.words), "original": list(concept.original),
                 "tags": list(concept.tags)},
        market=market or user_country(user), market_source="question" if market else "user",
        subject=subject, subject_origin=origin, direction=direction,
        inherited=["subject"] if origin == "conversation" else [],
    )  # fmt: skip


def concept_label(req: Request, lang: str) -> str:
    """The kind of product, as the answer names it: the category's name in the answer's
    language when OFF has it, else its English name, else the question's own words."""
    from catalog.models import Category

    tags = req.concept.get("tags") or []
    if tags:
        row = Category.objects.filter(tag=tags[0]).values_list("name", "names").first()
        if row:
            name, names = row
            return ((names or {}).get(lang) or [name])[0]
    return " ".join(req.concept.get("original") or req.concept.get("words") or [])


def request_products(req: Request):
    from catalog.services import concepts

    c = concepts.Concept(words=tuple(req.concept.get("words") or ()),
                         original=tuple(req.concept.get("original") or ()),
                         tags=tuple(req.concept.get("tags") or ()))  # fmt: skip
    return concepts.products(c, [req.market] if req.market else [])


def _per_100(products: list[Product], nutrient: str) -> dict[Any, tuple]:
    """Declared, as-sold values per 100 g (else per 100 ml) of the products' current
    revisions: ``{product_id: (value, unit, basis)}``, plain values only."""
    rows = NutrientFact.objects.filter(
        revision_id__in=[p.current_revision_id for p in products], nutrient=nutrient,
        preparation=AS_SOLD, status="declared", value__isnull=False, modifier__in=["", "~"],
        basis__in=PER_100,
    ).values_list("revision_id", "value", "unit", "basis")  # fmt: skip
    by_rev = {}
    for rid, value, unit, basis in rows:
        if rid not in by_rev or basis == "per_100g":
            by_rev[rid] = (value, unit, basis)
    return {
        p.pk: by_rev[p.current_revision_id] for p in products if p.current_revision_id in by_rev
    }


def _same_basis(values: dict[Any, tuple]) -> dict[Any, tuple]:
    """Only values that can be compared with each other: one basis (per 100 g when there is
    any, else per 100 ml) and one unit; the others count as having no comparable value."""
    if not values:
        return {}
    bases = {v[2] for v in values.values()}
    basis = "per_100g" if "per_100g" in bases else next(iter(bases))
    units = sorted({v[1] for v in values.values() if v[2] == basis})
    unit = units[0] if units else "g"
    return {k: v for k, v in values.items() if v[2] == basis and v[1] == unit}


def answer_request(req: Request, earlier: list[str], lang: str = "en"):
    """``(answer, cards, details)`` for a request, written from the data only."""
    from myola.services import chat_text as W

    details: dict[str, Any] = {"kind": "recommendation", "request": req.as_dict()}
    if req.intent == "clarify":
        return W.t(lang, "ask_which" if not req.inherited else "ask_criterion"), [], details
    if req.intent == "group":
        return _group_answer(req, lang, details)
    found = request_products(req)
    label = concept_label(req, lang)
    where = W.markets([req.market], lang) if req.market else ""
    total = found.count()
    details["request"]["filters"] = {
        "categories": req.concept.get("tags"), "name_words": (
            req.concept.get("words") if len(req.concept.get("words") or []) > 1 else []),
        "countries": [req.market] if req.market else [], "listable": True,
        "nutrient": req.subject if req.subject in NUTRIENTS else None,
    }  # fmt: skip
    details["candidates"] = total
    lines: list[str] = []
    cards: list[dict] = []
    picks: list[tuple[Product, Decimal | None, str]] = []
    if req.subject_origin == "conversation" and req.subject:
        what = W.nutrient(lang, req.subject) if req.subject in NUTRIENTS else req.subject
        lines.append(W.t(lang, "rec_context", what=what))
    if total == 0:
        lines.append(W.t(lang, "found_none", kind=label, where=where))
    elif req.subject in NUTRIENTS:
        products = list(found.select_related("current_revision")[:2000])
        values = _same_basis(_per_100(products, req.subject))
        name = W.nutrient(lang, req.subject)
        if not values:
            lines.append(W.t(lang, "found_no_values", n=total, kind=label, where=where, what=name))
            for p in products[:LIST_LIMIT]:
                picks.append((p, None, ""))
                cards.append(product_card(p, lang))
        else:
            ranked = sorted(products, key=lambda p: (p.pk not in values, (
                values[p.pk][0] if req.direction == "lower" else -values[p.pk][0])
                if p.pk in values else 0, p.name))[:3]  # fmt: skip
            label_c = W.t(lang, "rec_least" if req.direction == "lower" else "rec_most", what=name)
            lines.append(W.t(lang, "rec_head", kind=label, where=where, label=label_c))
            for p in ranked:
                value, unit, basis = values[p.pk]
                picks.append((p, value, basis))
                cards.append(product_card(p, lang, req.subject, value, unit, basis))
                lines.append(f"- {_name(p)}: {W.number(value, unit, lang)} {name} "
                             f"{W.basis(lang, basis)}")  # fmt: skip
            missing = len(products) - len(values)
            if missing:
                lines.append(W.t(lang, "found_missing", n=missing, what=name))
            details.update(nutrient=req.subject, basis="per_100g", unit="g")
    elif req.subject in CONCEPTS:
        _concept_lines(req, found, label, where, lang, lines, cards, picks)
    else:
        ordered = list(found.order_by(F("foodo_value").desc(nulls_last=True), "name")[:LIST_LIMIT])
        key = "found_list" if req.intent == "discover" else "rec_head"
        if req.intent == "discover":
            lines.append(W.t(lang, key, kind=label, where=where, n=total))
        else:
            lines.append(W.t(lang, key, kind=label, where=where, label=W.t(lang, "rec_foodo")))
        for p in ordered:
            picks.append((p, None, ""))
            if req.intent == "discover":
                cards.append(product_card(p, lang))
                lines.append(f"- {_name(p)}")
            else:
                cards.append(product_card(p, lang, "foodo", foodo=p.foodo_value))
                lines.append(
                    f"- {_name(p)}"
                    + (f": Foodo Score {p.foodo_value}/100" if p.foodo_value else "")
                )
        if total > len(ordered):
            lines.append(W.t(lang, "found_more", n=total - len(ordered)))
    details["products"] = [_product_detail(p, v, None) for p, v, _ in picks]
    return "\n".join(lines), cards, details


def _concept_lines(req, found, label, where, lang, lines, cards, picks) -> None:
    """A thing the catalogue does not map ("omega-3"): the OFF value when products have it;
    else said to be missing, with what the data does confirm (OFF's narrower category and
    the total fat per 100 g)."""
    from myola.services import chat_text as W

    concept = CONCEPTS[req.subject]
    key = concept["off_key"]
    with_value = list(found.filter(
        **{f"current_revision__off_payload__nutriments__{key}__isnull": False}
    ).select_related("current_revision")[:50])  # fmt: skip
    with_value.sort(key=lambda p: -float(p.current_revision.off_payload["nutriments"][key]))
    if with_value:
        lines.append(W.t(lang, "rec_head", kind=label, where=where,
                         label=W.t(lang, "rec_most", what=req.subject)))  # fmt: skip
        for p in with_value[:3]:
            value = p.current_revision.off_payload["nutriments"][key]
            picks.append((p, Decimal(str(value)), "per_100g"))
            cards.append(product_card(p, lang))
            lines.append(f"- {_name(p)}: {W.number(value, 'g', lang)} {req.subject} "
                         f"{W.basis(lang, 'per_100g')}")  # fmt: skip
        return
    lines.append(W.t(lang, "rec_missing", kind=label, where=where, what=req.subject))
    tags = req.concept.get("tags") or []
    narrower = next((concept["category"][t] for t in tags if t in concept["category"]), None)
    if not narrower:
        return
    pool = found.filter(categories__contains=[narrower])
    fats = list(
        NutrientFact.objects.filter(
            revision__product__in=pool, revision__product__current_revision=F("revision"),
            nutrient="fat", basis="per_100g", preparation=AS_SOLD, status="declared",
            value__isnull=False,
        ).select_related("revision__product").order_by("-value", "revision__product__name")[:3]
    )  # fmt: skip
    if fats:
        lines.append(W.t(lang, "rec_category", category=category_name(narrower), what=req.subject))
        for f in fats:
            p = f.revision.product
            picks.append((p, None, ""))
            cards.append(product_card(p, lang, "fat", f.value, f.unit))
            lines.append(
                f"- {_name(p)}: {W.t(lang, 'rec_fat', value=W.number(f.value, 'g', lang))}"
            )


def _group_answer(req: Request, lang: str, details: dict):
    """ "Care dintre ele are mai puțin zahăr?": only the products of the previous answer,
    compared on the value asked for, on compatible values (declared, as sold, per 100 g or
    100 ml, the same unit)."""
    import uuid as uuid_module

    from myola.services import chat_text as W

    ids = []
    for raw in req.scope:
        try:
            ids.append(uuid_module.UUID(str(raw)))
        except ValueError:
            continue
    by_id = Product.objects.select_related("current_revision").in_bulk(ids)
    products = [by_id[i] for i in ids if i in by_id]
    name = W.nutrient(lang, req.subject)
    values = _same_basis(_per_100(products, req.subject))
    units = {v[1] for v in values.values()}
    bases = {v[2] for v in values.values()}
    details["candidates"] = len(products)
    if not values:
        key = "group_no_values_one" if len(products) == 1 else "group_no_values"
        text = W.t(lang, key, n=len(products), what=name)
        return (
            text,
            [product_card(p, lang) for p in products],
            {**details, "products": [_product_detail(p, None, None) for p in products]},
        )
    ranked = sorted((p for p in products if p.pk in values),
                    key=lambda p: (values[p.pk][0] if req.direction == "lower"
                                   else -values[p.pk][0], p.name))  # fmt: skip
    basis = next(iter(bases))
    key = "group_head_one" if len(products) == 1 else "group_head"
    lines = [W.t(lang, key, n=len(products), what=name, basis=W.basis(lang, basis))]
    cards = []
    for p in ranked:
        value, unit, _ = values[p.pk]
        lines.append(f"- {_name(p)}: {W.number(value, unit, lang)}")
        cards.append(product_card(p, lang, req.subject, value, unit, basis))
    best = ranked[0]
    lines.append(W.t(lang, "group_best_low" if req.direction == "lower" else "group_best_high",
                     name=_name(best), what=name))  # fmt: skip
    missing = [p for p in products if p.pk not in values]
    if missing:
        lines.append(W.t(lang, "found_missing", n=len(missing), what=name))
    details.update(nutrient=req.subject, basis=basis, unit=next(iter(units)))
    details["products"] = [_product_detail(p, values[p.pk][0], None) for p in ranked]
    return "\n".join(lines), cards, details


# ---------- Follow-up: "And for 200 g?" ----------
_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|grame|grams?|ml|l)\b", re.IGNORECASE)


def amount_asked(text: str) -> tuple[Decimal, str] | None:
    """A short follow-up asking for another amount ("Dar la 200 g?", "and for 150 ml?")."""
    t = (text or "").strip()
    m = _AMOUNT.search(t)
    if m is None or len(t.split()) > 8 or criterion_of(t) is not None:
        return None
    number = Decimal(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit == "kg":
        number, unit = number * 1000, "g"
    elif unit == "l":
        number, unit = number * 1000, "ml"
    elif unit != "ml":
        unit = "g"
    return (number, unit) if number > 0 else None


def followup_answer(
    previous: dict[str, Any] | None, amount: Decimal, unit: str, lang: str = "en"
) -> str:
    """The values of the previous answer (a comparison or a value), for another amount,
    computed here: value per 100 × amount / 100, only on the same unit (g with per 100 g,
    ml with per 100 ml; never converted). A short question back when that is not possible."""
    from myola.services import chat_text as W

    if not previous or previous.get("kind") not in ("comparison", "value", "recommendation"):
        return W.t(lang, "which_product")
    basis = previous.get("basis") or ""
    nutrient = (previous.get("criterion") or {}).get("nutrient") or previous.get("nutrient")
    rows = [p for p in previous.get("products") or [] if p.get("value") is not None]
    if nutrient == "foodo" or not basis or not rows:
        return W.t(lang, "no_base")
    per = "g" if basis == "per_100g" else "ml"
    if per != unit:
        return W.t(lang, "other_unit", per=per)
    size = W.number(amount, "", lang)
    lines = [W.t(lang, "amount", What=W.capital(W.nutrient(lang, nutrient)), size=size,
                 unit=unit)]  # fmt: skip
    for p in rows:
        total = Decimal(p["value"]) * amount / 100
        label = (p["name"] or W.t(lang, "unnamed")) + (f" ({p['brand']})" if p["brand"] else "")
        lines.append(f"- {label}: {W.number(total, previous.get('unit') or 'g', lang)}")
    return "\n".join(lines)


def _excerpt(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "…"


def deterministic_answer(material: Material, lang: str = "en", excerpts=None) -> str:
    """The material written without the AI, in English or Romanian. ``excerpts``: the passages'
    text to quote (for example already translated), else the article's own words."""
    from myola.services import chat_text as W

    lines: list[str] = []
    if material.comparison is not None:
        lines.append(direct_answer(material.comparison, lang))
    for i, hit in enumerate(material.hits[:MAX_PASSAGES], start=1):
        excerpt = (excerpts or {}).get(i) or _excerpt(hit.passage.text) + W.t(lang, "in_english")
        lines.append(W.t(lang, "magazine", title=hit.article.title, excerpt=excerpt) + f" [A{i}]")
    return "\n".join(lines)

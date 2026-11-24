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
def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def criterion_of(text: str) -> Criterion | None:
    """The one criterion of a comparison question, or None when it is not one. "Less salt"
    and "a higher Foodo Score" are different criteria: a question naming a nutrient compares
    that nutrient; the Foodo Score only when it names the score and no nutrient."""
    t = f" {(text or '').lower()} "
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
    t = f" {(text or '').lower()} "
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


@dataclass
class Material:
    """What the model may use, with the ids it may cite, and the links behind those ids."""

    comparison: Comparison | None
    hits: list
    facts: dict[str, Any]
    sources: dict[str, dict[str, str]]


def build_material(comparison: Comparison | None, hits: list, product_facts=None) -> Material:
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
            sources["P0"] = {"kind": "product", "title": _name(c.reference),
                             "url": _product_url(c.reference)}  # fmt: skip
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
            sources[pid] = {"kind": "product", "title": _name(m.product),
                            "url": _product_url(m.product)}  # fmt: skip
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
            sources[aid] = {"kind": "article", "title": hit.article.title,
                            "url": reverse("learn-article", args=[hit.article.slug])}  # fmt: skip
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
    from myola.services import rag_text as W

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
    tally = W.t(lang, "tally", compared=compared, n=c.candidates)
    if c.unknown:
        tally += W.t(lang, "tally_unknown", unknown=c.unknown, what=what)
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
    t = f" {(text or '').lower()} "
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
    from myola.services import rag_text as W

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


# ---------- Recommendations from the whole catalogue ----------
# Foods named in a question -> OFF category tags (Romanian and English words).
FOODS: dict[str, tuple[str, ...]] = {
    "en:fishes": ("pește", "peste", "pești", "pesti", "peşte", "fish"),
    "en:pizzas": ("pizza",),
    "en:yogurts": ("iaurt", "yogurt", "yoghurt"),
    "en:breakfast-cereals": ("cereale", "cereal"),
    "en:cheeses": ("brânz", "branz", "cheese"),
    "en:breads": ("pâine", "paine", "bread"),
    "en:chocolates": ("ciocolat", "chocolate"),
    "en:biscuits": ("biscui",),
}
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
)  # fmt: skip
ANAPHORA = (
    "aceste", "acestea", "astea", "acelea", "aceleași", "aceleasi", "acesta", "these", "those",
    "them", "this fat", "such",
)  # fmt: skip
THIS_PRODUCT = ("scanat", "scanned", "this one", "this product", "acest produs", "acesta",
                "decât produsul", "decat produsul", "than this")  # fmt: skip


def food_of(text: str) -> str | None:
    t = f" {(text or '').lower()} "
    for tag, words in FOODS.items():
        if _has(t, words):
            return tag
    return None


def wants_recommendation(text: str, product: Product | None) -> bool:
    """ "Recomandă-mi…", "Ce produs are…", "Unde găsesc…": a search of the whole catalogue
    (not a comparison with the open product: those name "this one" or a criterion)."""
    t = f" {(text or '').lower()} "
    if not _has(t, RECOMMEND_WORDS):
        return False
    if product is not None and (criterion_of(text) is not None or _has(t, THIS_PRODUCT)):
        return False
    return food_of(text) is not None


def concept_of(text: str) -> str | None:
    t = (text or "").lower()
    for key, concept in CONCEPTS.items():
        if _has(t, concept["words"]):
            return key
    return None


def subject_of(text: str, earlier: list[str]) -> tuple[str | None, str]:
    """What the question asks for: a nutrient key or a concept ("omega-3"), and where it came
    from ("question" or "conversation"). "These fats" and other references are resolved from
    the user's earlier messages of the same chat, newest first."""
    t = f" {(text or '').lower()} "
    own = concept_of(text)
    if own:
        return own, "question"
    for key, (_, words) in NUTRIENTS.items():
        if key == "fat" and _has(t, NUTRIENTS["saturated-fat"][1]):
            continue
        if _has(t, words) and not _has(t, ANAPHORA):
            return key, "question"
    if _has(t, ANAPHORA) or not any(_has(t, w) for _, w in NUTRIENTS.values()):
        for message in earlier:
            found = concept_of(message)
            if found:
                return found, "conversation"
            m = f" {message.lower()} "
            for key, (_, words) in NUTRIENTS.items():
                if _has(m, words):
                    return key, "conversation"
    for key, (_, words) in NUTRIENTS.items():
        if _has(t, words):
            return key, "question"
    return None, ""


def recommend(text: str, earlier: list[str], user=None, lang: str = "en"):
    """``(answer, sources, details)``: 1-3 real products of the catalogue for the food asked
    for, sold in the market of the question (else the user's), ranked by the value asked for.
    Each line says what the data confirms; a value the catalogue does not have is said to be
    missing, never guessed."""
    from myola.context.django_source import user_country
    from myola.services import rag_text as W

    category = food_of(text)
    market = market_of(text) or user_country(user)
    subject, origin = subject_of(text, earlier)
    lower = _has(f" {(text or '').lower()} ", LOWER_WORDS)
    found = Product.objects.filter(
        current_revision__isnull=False, listable=True, categories__contains=[category],
        countries__contains=[market],
    )  # fmt: skip
    kind = category_name(category)
    where = W.markets([market], lang)
    details: dict[str, Any] = {"kind": "recommendation", "category": category,
                               "markets": [market], "subject": subject, "origin": origin,
                               "candidates": found.count()}  # fmt: skip
    picks: list[tuple[Product, str]] = []
    lines: list[str] = []
    if subject in NUTRIENTS:
        facts = list(
            NutrientFact.objects.filter(
                revision__product__in=found, revision__product__current_revision=F("revision"),
                nutrient=subject, basis="per_100g", preparation=AS_SOLD, status="declared",
                value__isnull=False, modifier__in=["", "~"],
            )
            .select_related("revision__product")
            .order_by("value" if lower else "-value", "revision__product__name")[:3]
        )  # fmt: skip
        name = W.nutrient(lang, subject)
        details.update(nutrient=subject, basis="per_100g", unit="g")
        if not facts:
            lines.append(W.t(lang, "rec_no_value", kind=kind, where=where, what=name))
        else:
            label = W.t(lang, "rec_least" if lower else "rec_most", what=name)
            lines.append(W.t(lang, "rec_head", kind=kind, where=where, label=label))
            for f in facts:
                p = f.revision.product
                picks.append((p, str(f.value)))
                lines.append(f"- {_name(p)}: {W.number(f.value, f.unit, lang)} {name} "
                             f"{W.basis(lang, 'per_100g')}")  # fmt: skip
    elif subject in CONCEPTS:
        concept = CONCEPTS[subject]
        key = concept["off_key"]
        with_value = list(
            found.filter(
                **{f"current_revision__off_payload__nutriments__{key}__isnull": False}
            ).select_related("current_revision")[:50]
        )
        with_value.sort(key=lambda p: -float(p.current_revision.off_payload["nutriments"][key]))
        if with_value:
            lines.append(W.t(lang, "rec_head", kind=kind, where=where,
                             label=W.t(lang, "rec_most", what=subject)))  # fmt: skip
            for p in with_value[:3]:
                value = p.current_revision.off_payload["nutriments"][key]
                picks.append((p, str(value)))
                lines.append(f"- {_name(p)}: {W.number(value, 'g', lang)} {subject} "
                             f"{W.basis(lang, 'per_100g')}")  # fmt: skip
        else:
            lines.append(W.t(lang, "rec_missing", kind=kind, where=where, what=subject))
            narrower = concept["category"].get(category)
            pool = found.filter(categories__contains=[narrower]) if narrower else found
            fats = list(
                NutrientFact.objects.filter(
                    revision__product__in=pool,
                    revision__product__current_revision=F("revision"), nutrient="fat",
                    basis="per_100g", preparation=AS_SOLD, status="declared",
                    value__isnull=False,
                )
                .select_related("revision__product")
                .order_by("-value", "revision__product__name")[:3]
            )  # fmt: skip
            if narrower and fats:
                lines.append(W.t(lang, "rec_category", category=category_name(narrower),
                                 what=subject))  # fmt: skip
                for f in fats:
                    p = f.revision.product
                    picks.append((p, None))
                    lines.append(
                        f"- {_name(p)}: {W.t(lang, 'rec_fat', value=W.number(f.value, 'g', lang))}"
                    )
    else:
        best = list(found.filter(foodo_value__isnull=False).order_by("-foodo_value", "name")[:3])
        if best:
            lines.append(W.t(lang, "rec_head", kind=kind, where=where,
                             label=W.t(lang, "rec_foodo")))  # fmt: skip
            for p in best:
                picks.append((p, None))
                lines.append(f"- {_name(p)}: Foodo Score {p.foodo_value}/100")
    if not lines:
        lines.append(W.t(lang, "rec_none", kind=kind, where=where))
    if origin == "conversation" and subject:
        lines.insert(0, W.t(lang, "rec_context", what=W.nutrient(lang, subject)
                            if subject in NUTRIENTS else subject))  # fmt: skip
    sources = [{"kind": "product", "title": _name(p), "url": _product_url(p)} for p, _ in picks]
    details["products"] = [_product_detail(p, Decimal(v) if v else None, None) for p, v in picks]
    return "\n".join(lines), sources, details


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
    from myola.services import rag_text as W

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
    from myola.services import rag_text as W

    lines: list[str] = []
    if material.comparison is not None:
        lines.append(direct_answer(material.comparison, lang))
    for i, hit in enumerate(material.hits[:MAX_PASSAGES], start=1):
        excerpt = (excerpts or {}).get(i) or _excerpt(hit.passage.text)
        lines.append(W.t(lang, "magazine", title=hit.article.title, excerpt=excerpt) + f" [A{i}]")
    return "\n".join(lines)

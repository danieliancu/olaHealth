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
    "sugars": ("sugars", ("sugar", "zahăr", "zahar", "zaharu", "sucre", "azúcar", "zucchero",
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
    market: str
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


def compare_products(text: str, product: Product | None, user=None) -> Comparison | None:
    """The comparison asked for, computed from the database (None: not a comparison)."""
    from catalog.services.alternatives import _user_filters
    from myola.context.django_source import user_country

    criterion = criterion_of(text)
    if criterion is None:
        return None
    market = market_of(text) or user_country(user)
    category = category_of(text, product)
    if category is None:
        return None if product is None else Comparison(
            criterion, market, "", "", product, None, "", "", None, problem="no_category"
        )  # fmt: skip
    result = Comparison(
        criterion, market, category, category_name(category), product, None, "", "", None
    )
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
    candidates = list(
        Product.objects.filter(
            current_revision__isnull=False,
            listable=True,
            categories__contains=[category],
            countries__contains=[market],
        )
        .exclude(pk=product.pk)
        .select_related("current_revision")
        .order_by("pk")[:MAX_CANDIDATES]
    )
    candidates = [p for p in candidates if str(p.pk) not in avoid_ids]
    result.candidates = len(candidates)
    matches: list[Match] = []
    if criterion.nutrient == "foodo":
        from assessments.score import is_better

        for p in candidates:
            value, score = _foodo(p.current_revision)
            if value is None or not is_better(score, ref_score):
                result.unknown += value is None
                continue
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
                continue
            value = row[0]
            better = _meets(value, row[3], result.reference_value, criterion.direction)
            if better is None:
                result.unknown += 1  # "< x" / "> x" that cannot be compared
                continue
            if better:
                matches.append(Match(p, value, row[1], None, value - result.reference_value))
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
            "market": c.market.split(":", 1)[-1].replace("-", " ").title(),
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
    """The answer without ids that are not in the material, and the sources to link: the
    products of the comparison always (they are its result), articles only when cited."""
    used = set()

    def keep(m: re.Match) -> str:
        if m.group(1) in material.sources:
            used.add(m.group(1))
            return m.group(0)
        return ""

    clean = re.sub(r"[ \t]{2,}", " ", _CITE.sub(keep, text)).strip()
    out = [s for k, s in material.sources.items() if k.startswith("P")]
    out += [s for k, s in material.sources.items() if k.startswith("A") and k in used]
    return clean, out


def _market_name(tag: str) -> str:
    if tag == "en:united-kingdom":
        return "the UK"
    return tag.split(":", 1)[-1].replace("-", " ").title()


def _excerpt(text: str, limit: int = 320) -> str:
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "…"


def deterministic_answer(material: Material) -> str:
    """The material in plain English, without the AI (it is translated like rule answers)."""
    lines: list[str] = []
    c = material.comparison
    if c is not None:
        foodo = c.criterion.nutrient == "foodo"
        what = "Foodo Score" if foodo else NUTRIENTS[c.criterion.nutrient][0]
        where = f"{c.category_name.lower()} sold in {_market_name(c.market)}"
        if c.problem == "no_reference":
            lines.append("Open or scan a product first: I compare other products with it.")
        elif c.problem == "no_category":
            lines.append("This product's category is not known, so I can't compare it.")
        elif c.problem == "no_reference_value":
            lines.append(f"This product's {what} is not known, so I can't compare it [P0].")
        elif not c.matches:
            lines.append(
                f"None of the {c.candidates} other {where} in the catalogue has "
                f"{c.criterion.label} than this product [P0]."
            )
        else:
            basis = f", {BASIS_LABELS[c.basis]}" if c.basis else ""
            ref = _amount(c.reference_value, c.unit) if c.basis else f"{c.reference_foodo}/100"
            lines.append(
                f"{c.criterion.label.capitalize()} than this product ({what} {ref}{basis}) "
                f"[P0], among {where}:"
            )
            for i, m in enumerate(c.matches, start=1):
                val = _amount(m.value, m.unit) if c.basis else f"{m.foodo}/100"
                lines.append(f"- {_name(m.product)}: {val} [P{i}]")
        if c.unknown and not c.problem:
            lines.append(f"{c.unknown} of them have no comparable {what} and were left out.")
    for i, hit in enumerate(material.hits[:MAX_PASSAGES], start=1):
        title, excerpt = hit.article.title, _excerpt(hit.passage.text)
        lines.append(f"From the Magazine, “{title}”: {excerpt} [A{i}]")
    return "\n".join(lines)

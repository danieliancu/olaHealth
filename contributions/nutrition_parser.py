"""Proposes nutrient/value/unit mappings from OCR text. It never decides on its own.

* OCR extracts text; this parser only *proposes* mappings for the user to confirm.
* Labels are matched against a versioned dictionary (EN/RO). Unrecognised lines that
  contain numbers are returned as *unmapped*, never guessed.
* Only the first value of a line is proposed (first column). Further values are kept
  as ``other_values`` so the user can choose.
* No conversions (kJ<->kcal, salt<->sodium, g<->ml) are made.
* Allergen / "may contain" statements are not interpreted here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from catalog.nutrition import decimal_str, to_decimal

DICTIONARY_PATH = Path(__file__).parent / "dictionaries" / "nutrients.v1.json"

_VALUE = re.compile(
    r"(?P<mod><|>|≤|≥|~)?\s*(?P<num>\d{1,5}(?:[.,]\d{1,3})?)\s*(?P<unit>kj|k\]|kcal|mg|µg|mcg|g)(?![a-z])",
    re.IGNORECASE,
)


@dataclass
class Proposal:
    line: str
    nutrient: str | None
    value: str | None
    unit: str | None
    modifier: str | None = None
    other_values: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    dictionary_version: str
    basis: str | None
    proposals: list[Proposal]
    unmapped: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dictionary_version": self.dictionary_version,
            "basis": self.basis,
            "proposals": [asdict(p) for p in self.proposals],
            "unmapped": self.unmapped,
        }


def fold(text: str) -> str:
    text = text.replace("ş", "s").replace("ţ", "t").replace("Ş", "S").replace("Ţ", "T")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@cache
def load_dictionary() -> dict[str, Any]:
    return json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))


def _aliases() -> list[tuple[str, str]]:
    d = load_dictionary()
    pairs = [
        (fold(alias), nutrient)
        for labels in d["labels"].values()
        for nutrient, aliases in labels.items()
        for alias in aliases
    ]
    return sorted(set(pairs), key=lambda p: -len(p[0]))  # longest alias first


def _prefixes() -> list[str]:
    d = load_dictionary()
    return sorted(
        {fold(p) for ps in d["strip_prefixes"].values() for p in ps}, key=len, reverse=True
    )


def detect_basis(text: str) -> str | None:
    folded = fold(text)
    found = []
    for basis, markers in load_dictionary()["basis_markers"].items():
        positions = [folded.find(fold(m)) for m in markers if fold(m) in folded]
        if positions:
            found.append((min(positions), basis))
    return sorted(found)[0][1] if found else None


def _match_label(label: str) -> str | None:
    label = label.strip(" :.-–—\t")
    for prefix in _prefixes():
        if label.startswith(prefix + " ") or label.startswith(prefix + ":"):
            label = label[len(prefix) :].strip(" :.-–—\t")
            break
    for alias, nutrient in _aliases():
        if label == alias or label.startswith(alias + " ") or label.startswith(alias + "("):
            return nutrient
    return None


def parse_nutrition_text(text: str) -> ParseResult:
    proposals: list[Proposal] = []
    unmapped: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        values = list(_VALUE.finditer(line))
        if not values:
            continue
        label_part = fold(line[: values[0].start()])
        nutrient = _match_label(label_part)
        if nutrient is None:
            unmapped.append(line)
            continue
        if nutrient == "energy":
            for unit_keys, key in ((("kj", "k]"), "energy-kj"), (("kcal",), "energy-kcal")):
                matches = [m for m in values if m.group("unit").lower() in unit_keys]
                if matches and key not in seen:
                    proposals.append(_proposal(line, key, matches))
                    seen.add(key)
            continue
        mass = [m for m in values if m.group("unit").lower() in ("g", "mg", "µg", "mcg")]
        if not mass or nutrient in seen:
            unmapped.append(line)
            continue
        proposals.append(_proposal(line, nutrient, mass))
        seen.add(nutrient)
    return ParseResult(load_dictionary()["version"], detect_basis(text), proposals, unmapped)


def _proposal(line: str, nutrient: str, matches: list[re.Match[str]]) -> Proposal:
    first = matches[0]
    value = to_decimal(first.group("num"))
    unit = first.group("unit").lower()
    # "k]" is a frequent OCR misreading of "kJ"; the user confirms every value anyway.
    unit = {"kj": "kJ", "k]": "kJ", "kcal": "kcal", "mcg": "µg"}.get(unit, unit)
    others = [f"{m.group('num').replace(',', '.')} {m.group('unit')}" for m in matches[1:]]
    return Proposal(
        line=line,
        nutrient=nutrient,
        value=decimal_str(value) if isinstance(value, Decimal) else None,
        unit=unit,
        modifier=first.group("mod"),
        other_values=others,
    )

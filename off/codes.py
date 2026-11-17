"""Barcode parsing and normalisation.

Two different normalisations are kept apart:

* ``gtin14`` – the GS1 canonical form (left-padded to 14 digits) of a code whose check
  digit is valid. It is our local identity key for global trade item numbers. Padding
  on the left is the GS1 rule, not an arbitrary removal of zeros.
* ``off_code`` – the form Open Food Facts stores and queries, following the documented
  OFF rule: leading zeros removed, then codes of 7 digits or less padded to 8 and codes
  of 9-12 digits padded to 13. Other lengths are left as they are.

The original value and the detected symbology are always preserved.
Codes from GS1 restricted-circulation ranges (in-store, variable measure, coupons)
are not globally unique; they are flagged ``restricted`` and never merged automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["gtin", "restricted", "unverified"]

FORMATS = {
    "ean_13": "ean13",
    "ean-13": "ean13",
    "ean13": "ean13",
    "ean_8": "ean8",
    "ean-8": "ean8",
    "ean8": "ean8",
    "upc_a": "upca",
    "upc-a": "upca",
    "upca": "upca",
    "upc_e": "upce",
    "upc-e": "upce",
    "upce": "upce",
    "itf": "itf14",
    "itf14": "itf14",
    "itf-14": "itf14",
    "manual": "manual",
    "": "manual",
}

_DIGITS = re.compile(r"^\d+$")


class InvalidCode(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ParsedCode:
    raw: str
    digits: str
    detected_format: str
    kind: Kind
    gtin14: str | None
    off_code: str
    check_digit_valid: bool | None

    @property
    def identity_key(self) -> str:
        """Key used for the local identifier table."""
        return self.gtin14 or self.digits


def gs1_check_digit(body: str) -> int:
    """Mod-10 check digit used by GTIN-8/12/13/14 (computed from the right)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def has_valid_check_digit(code: str) -> bool:
    return len(code) >= 2 and gs1_check_digit(code[:-1]) == int(code[-1])


def expand_upce(code: str) -> str | None:
    """Expand an 8-digit UPC-E (number system 0 or 1) into 12-digit UPC-A."""
    if len(code) != 8 or code[0] not in "01":
        return None
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = f"{d[0]}{d[1]}{last}0000{d[2]}{d[3]}{d[4]}"
    elif last == "3":
        body = f"{d[0]}{d[1]}{d[2]}00000{d[3]}{d[4]}"
    elif last == "4":
        body = f"{d[0]}{d[1]}{d[2]}{d[3]}00000{d[4]}"
    else:
        body = f"{d[0]}{d[1]}{d[2]}{d[3]}{d[4]}0000{last}"
    return f"{ns}{body}{check}"


def off_normalize(digits: str) -> str:
    """Normalise like the Open Food Facts server (documented barcode normalisation)."""
    stripped = digits.lstrip("0")
    if len(stripped) <= 7:
        return stripped.zfill(8)
    if 9 <= len(stripped) <= 12:
        return stripped.zfill(13)
    return stripped


def _is_restricted(gtin14: str) -> bool:
    """GS1 restricted-circulation and coupon prefixes (not globally unique)."""
    if gtin14.startswith("000000"):  # GTIN-8 stored in 14 digits
        gtin8 = gtin14[6:]
        return gtin8[0] in "02"  # RCN-8: prefixes 0 and 2 are for internal use
    gtin13 = gtin14[1:] if gtin14[0] == "0" else None
    if gtin13 is None:
        return False
    prefix3 = int(gtin13[:3])
    return (
        20 <= prefix3 <= 29  # 020-029 restricted circulation within a company
        or 40 <= prefix3 <= 49  # 040-049 restricted circulation within a company
        or 200 <= prefix3 <= 299  # 200-299 restricted circulation (variable measure)
        or 980 <= prefix3 <= 999  # refund receipts, coupons
    )


def parse_code(
    raw: str, detected_format: str = "manual", *, allow_bad_check: bool = False
) -> ParsedCode:
    value = (raw or "").strip().replace(" ", "").replace("-", "")
    fmt = FORMATS.get((detected_format or "").strip().lower(), "other")
    if not value:
        raise InvalidCode("empty", "Enter a barcode number.")
    if not _DIGITS.match(value):
        raise InvalidCode("not_numeric", "A barcode contains digits only.")
    if len(value) > 14:
        raise InvalidCode("too_long", "Barcodes have at most 14 digits.")
    if len(value) < 6:
        raise InvalidCode("too_short", "This number is too short to be a product barcode.")

    gtin_candidate: str | None = None
    if fmt == "upce" or (fmt == "manual" and len(value) == 6):
        # 6-digit UPC-E without number system/check digit cannot be verified reliably.
        if len(value) == 8:
            gtin_candidate = expand_upce(value)
    elif len(value) in (8, 12, 13, 14):
        gtin_candidate = value

    check_ok: bool | None = None
    gtin14: str | None = None
    kind: Kind = "unverified"
    if gtin_candidate is not None:
        check_ok = has_valid_check_digit(gtin_candidate)
        if check_ok:
            gtin14 = gtin_candidate.zfill(14)
            kind = "restricted" if _is_restricted(gtin14) else "gtin"
        elif not allow_bad_check:
            raise InvalidCode(
                "bad_check_digit",
                "The last digit does not match this barcode. Please check the number.",
            )

    return ParsedCode(
        raw=raw,
        digits=value,
        detected_format=fmt,
        kind=kind,
        gtin14=gtin14,
        off_code=off_normalize(value),
        check_digit_valid=check_ok,
    )

"""Server-side barcode decoding of a single uploaded photo with zxing-cpp."""

from __future__ import annotations

from dataclasses import dataclass

import zxingcpp
from PIL import Image

# zxing-cpp format names -> our format names
_FORMAT_NAMES = {
    "EAN13": "ean13",
    "EAN8": "ean8",
    "UPCA": "upca",
    "UPCE": "upce",
    "ITF": "itf14",
    "ITF14": "itf14",  # zxing-cpp 3 reports ITF-14 as its own format
}


@dataclass(frozen=True)
class DecodedBarcode:
    text: str
    format: str


def _format_name(fmt: object) -> str:
    name = getattr(fmt, "name", str(fmt)).split(".")[-1].upper().replace("-", "").replace("_", "")
    return _FORMAT_NAMES.get(name, "")


def decode_product_barcodes(image: Image.Image) -> list[DecodedBarcode]:
    """Return product barcodes (EAN/UPC/ITF-14) found in the image, de-duplicated."""
    results = zxingcpp.read_barcodes(image)
    found: dict[str, DecodedBarcode] = {}
    for r in results:
        fmt = _format_name(r.format)
        text = (r.text or "").strip()
        if not fmt or not text.isdigit():
            continue
        if fmt == "itf14" and len(text) != 14:
            continue
        found.setdefault(text, DecodedBarcode(text, fmt))
    return list(found.values())

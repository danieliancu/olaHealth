"""Generate the OCR fixture images (synthetic labels made by us, no third-party rights).

Run inside the container:  python tests/fixtures/ocr/make_fixtures.py
The output is committed; re-running overwrites the PNG/JPEG files deterministically.
They imitate phone photos: off-white paper, slight rotation, blur, JPEG compression.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent

LABELS = {
    "label_en.jpg": [
        "Nutrition information   per 100 g",
        "Energy          1966 kJ / 470 kcal",
        "Fat                         21 g",
        "of which saturates         9.8 g",
        "Carbohydrate                61 g",
        "of which sugars             28 g",
        "Fibre                      2.5 g",
        "Protein                    6.2 g",
        "Salt                      0.48 g",
    ],
    "label_ro.jpg": [
        "Declaratie nutritionala  la 100 g",
        "Valoare energetica  1580 kJ / 377 kcal",
        "Grasimi                     12 g",
        "din care acizi grasi saturati 4,5 g",
        "Glucide                   58,2 g",
        "din care zaharuri           22 g",
        "Fibre                      3,1 g",
        "Proteine                     7 g",
        "Sare                       0,9 g",
    ],
}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render(lines: list[str], seed: int) -> Image.Image:
    rnd = random.Random(seed)
    f = font(34)
    img = Image.new("RGB", (1100, 90 + 58 * len(lines)), (246, 243, 232))
    d = ImageDraw.Draw(img)
    y = 40
    for i, line in enumerate(lines):
        d.text((50, y), line, font=f, fill=(25, 25, 25))
        if i == 0:
            d.line((40, y + 48, 1060, y + 48), fill=(40, 40, 40), width=3)
        y += 58
    img = img.rotate(
        rnd.uniform(-1.5, 1.5),
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=(120, 130, 125),
    )
    return img.filter(ImageFilter.GaussianBlur(0.8))


def main() -> None:
    for i, (name, lines) in enumerate(sorted(LABELS.items())):
        render(lines, seed=i).save(HERE / name, format="JPEG", quality=80)
        print("wrote", name)


if __name__ == "__main__":
    main()

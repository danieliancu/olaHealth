"""Widen a cover photo by extending its background to the sides (for the desktop Magazine cover).

    python tools/widen_cover.py SOURCE.png OUT.png [--ratio 3.0]

The original stays untouched in the middle. Each new side is the top of that edge of the photo
(plain background only) stretched and blurred, and the original fades into it over a soft
edge, so a photo shot on a plain background (like the green of the cover photo) reaches any
width without a visible seam or a smear of the subject. Needs Pillow.
"""

from __future__ import annotations

import argparse

from PIL import Image, ImageFilter


def widen(src: Image.Image, ratio: float, feather: int = 160, sky: float = 0.5) -> Image.Image:
    """``sky``: the top share of each edge that is plain background (the person never reaches
    it); only that part is stretched into the new sides, so no colour of the subject spills."""
    src = src.convert("RGB")
    w, h = src.size
    out_w = max(w, round(h * ratio))
    left = (out_w - w) // 2
    right = out_w - w - left
    strip = max(8, w // 20)
    band = max(1, round(h * sky))
    bg = Image.new("RGB", (out_w, h))
    # Each side: the top of that edge of the photo, stretched to the side's size and blurred.
    if left:
        side = src.crop((0, 0, strip, band)).resize((left + feather, h), Image.Resampling.BICUBIC)
        bg.paste(side.filter(ImageFilter.GaussianBlur(40)), (0, 0))
    if right:
        side = src.crop((w - strip, 0, w, band))
        side = side.resize((right + feather, h), Image.Resampling.BICUBIC)
        bg.paste(side.filter(ImageFilter.GaussianBlur(40)), (out_w - right - feather, 0))
    # The original in the middle, fading into the sides at its left and right edges.
    mask = Image.new("L", (w, h), 255)
    ramp = [round(255 * i / feather) for i in range(feather)]
    px = mask.load()
    for x, value in enumerate(ramp):
        for y in range(h):
            px[x, y] = value
            px[w - 1 - x, y] = value
    bg.paste(src, (left, 0), mask)
    return bg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("out")
    parser.add_argument("--ratio", type=float, default=3.0, help="width / height (default 3)")
    args = parser.parse_args()
    with Image.open(args.source) as img:
        widen(img, args.ratio).save(args.out)
    print(f"{args.out}: {args.ratio}:1")


if __name__ == "__main__":
    main()

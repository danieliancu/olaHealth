"""One-off: remove the plain studio background of the home photos (flood fill from the edges).

Usage (in the web container):
    python tools/cutout.py assets-src/home/apple-drink.png static/img/home/apple-drink
The Greek yoghurt uses the hand-drawn YOGHURT outline (see below) via cutout(..., protect=YOGHURT).
"""

import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SENTINEL = (255, 0, 255)


def cutout(
    src: str, dest: str, thresh: int = 38, size: int = 1000, edge: int = 0, protect=None
) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    work = img.copy()
    if edge:
        # Outlines act as a wall: the fill cannot cross them into a product of the same colour.
        edges = img.convert("L").filter(ImageFilter.GaussianBlur(2)).filter(ImageFilter.FIND_EDGES)
        wall = edges.point(lambda v: 255 if v > edge else 0).filter(ImageFilter.MaxFilter(3))
        # The image border is not an outline: keep it open so the fill can start there.
        ImageDraw.Draw(wall).rectangle((0, 0, w - 1, h - 1), outline=0, width=6)
        work.paste((0, 0, 0), mask=wall)
    # Fill the background from points all around the border (the backdrop is one soft colour).
    step = max(1, min(w, h) // 40)
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    for seed in seeds:
        if work.getpixel(seed) != SENTINEL:
            ImageDraw.floodfill(work, seed, SENTINEL, thresh=thresh)
    # Alpha: 0 where the fill reached, 255 elsewhere; soften the edge a little.
    r, g, b = work.split()
    is_bg = ImageChops.multiply(
        ImageChops.multiply(
            r.point(lambda v: 255 if v == 255 else 0), g.point(lambda v: 255 if v == 0 else 0)
        ),
        b.point(lambda v: 255 if v == 255 else 0),
    )
    alpha = ImageChops.invert(is_bg)
    if edge:
        # The wall itself is outside the product: grow the background over it, then soften.
        alpha = ImageChops.invert(is_bg.filter(ImageFilter.MaxFilter(5)))
    alpha = alpha.filter(ImageFilter.MinFilter(3))
    if protect:
        # Areas drawn by hand (in 500 px preview coordinates) always stay: for packs whose
        # colour is the same as the backdrop, where no automatic cut is reliable.
        k = w / 500
        keep = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(keep)
        for kind, pts in protect:
            pts = [round(v * k) for v in pts]
            if kind == "ellipse":
                d.ellipse(pts, fill=255)
            else:
                d.polygon(list(zip(pts[::2], pts[1::2], strict=True)), fill=255)
        alpha = ImageChops.lighter(alpha, keep)
    alpha = alpha.filter(ImageFilter.GaussianBlur(1.2))
    out = img.copy()
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    if box:
        pad = int(max(box[2] - box[0], box[3] - box[1]) * 0.04)
        box = (
            max(0, box[0] - pad),
            max(0, box[1] - pad),
            min(w, box[2] + pad),
            min(h, box[3] + pad),
        )
        out = out.crop(box)
    # Square canvas, product centred, transparent around it.
    side = max(out.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(out, ((side - out.width) // 2, (side - out.height) // 2), out)
    canvas = canvas.resize((size, size), Image.LANCZOS)
    canvas.save(dest + ".png", optimize=True)
    canvas.save(dest + ".webp", quality=86, method=6)
    print(dest, canvas.size)


if __name__ == "__main__":
    cutout(
        sys.argv[1],
        sys.argv[2],
        thresh=int(sys.argv[3]) if len(sys.argv) > 3 else 38,
        edge=int(sys.argv[4]) if len(sys.argv) > 4 else 0,
    )


# Greek yoghurt: lid and tub outline (the tub is the backdrop colour on its right side).
YOGHURT = [
    ("ellipse", (37, 37, 449, 108)),
    ("polygon", (37, 72, 449, 72, 449, 140, 432, 152, 58, 152, 37, 140)),
    ("polygon", (59, 148, 431, 148, 397, 440, 89, 440)),
    ("ellipse", (89, 428, 397, 452)),
]

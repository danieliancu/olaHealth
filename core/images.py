"""Validation and re-encoding of every image that enters the site (contributions, scan photos,
Learn article photos, Open Food Facts photos cached locally).

* Real type detected by Pillow from content, never from the name or the client MIME type.
* Only JPEG, PNG, WebP and AVIF. SVG and anything else are rejected.
* Size, pixel count and minimum side limits; decompression bombs rejected.
* EXIF orientation applied, then all metadata (incl. GPS) dropped by re-encoding.
* Web copies (``web_webp``) are WebP and never wider than the largest size they are shown at
  (see the *_MAX_WIDTH constants below; measured on the live layouts, ×2 or ×3 for sharp screens).
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "AVIF"}  # always re-encoded, never served as sent

# Largest width each kind of photo is ever displayed at, times the screen density it needs:
# Learn hero: 1000 CSS px on desktop (728 tablet, 350 phone) × 2 = 2000.
LEARN_MAX_WIDTH = 2000
# Product photos: at most 160 CSS px (gallery), 112 on the product page, 64 in lists; phones
# are up to 3× dense: 160 × 3 = 480.
PRODUCT_MAX_WIDTH = 480
WEBP_QUALITY = 80


class ImageRejected(ValueError):
    pass


@dataclass(frozen=True)
class CleanImage:
    content: bytes
    width: int
    height: int
    sha256: str
    source_format: str
    image: Image.Image  # decoded RGB image, already oriented


def _read_limited(upload: UploadedFile | io.BytesIO, max_bytes: int) -> bytes:
    size = getattr(upload, "size", None)
    if size is not None and size > max_bytes:
        raise ImageRejected(_("The file is too large."))
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageRejected(_("The file is too large."))
    if not data:
        raise ImageRejected(_("The file is empty."))
    return data


def decode(data: bytes) -> tuple[Image.Image, str]:
    """Check an image's real type and size and return it as an oriented RGB image with no
    metadata, plus its source format. Raises ImageRejected."""
    max_pixels = settings.UPLOAD_MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                if fmt not in ALLOWED_FORMATS:
                    raise ImageRejected(_("Only JPEG, PNG, WebP or AVIF photos are accepted."))
                if probe.width * probe.height > max_pixels:
                    raise ImageRejected(_("The image resolution is too high."))
                probe.verify()
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                oriented = ImageOps.exif_transpose(img)
                rgb = oriented.convert("RGB")
        except ImageRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageRejected(_("The image resolution is too high.")) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ImageRejected(_("The file is not a readable image.")) from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous
    return rgb, fmt or ""


def web_webp(rgb: Image.Image, max_width: int, quality: int = WEBP_QUALITY) -> bytes:
    """A WebP copy for the web, never wider than ``max_width`` (smaller images are not enlarged)."""
    img = rgb
    if img.width > max_width:
        img = rgb.resize(
            (max_width, max(1, round(rgb.height * max_width / rgb.width))), Image.Resampling.LANCZOS
        )
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=quality, method=6)
    return out.getvalue()


def upload_to_webp(
    upload: UploadedFile | io.BytesIO, max_width: int, max_bytes: int | None = None
) -> bytes:
    """An uploaded file, validated, stripped of metadata and re-encoded as a web WebP."""
    rgb, _fmt = decode(_read_limited(upload, max_bytes or settings.UPLOAD_MAX_BYTES))
    return web_webp(rgb, max_width)


def validate_and_reencode(upload: UploadedFile | io.BytesIO) -> CleanImage:
    rgb, fmt = decode(_read_limited(upload, settings.UPLOAD_MAX_BYTES))
    if min(rgb.size) < settings.UPLOAD_MIN_SIDE:
        raise ImageRejected(_("The image is too small to be readable."))
    max_side = settings.UPLOAD_STORED_MAX_SIDE
    if max(rgb.size) > max_side:
        rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    # A fresh RGB image carries no EXIF/ICC/XMP: metadata (including GPS) is dropped.
    rgb.save(out, format="JPEG", quality=90, optimize=True)
    content = out.getvalue()
    return CleanImage(
        content=content,
        width=rgb.width,
        height=rgb.height,
        sha256=hashlib.sha256(content).hexdigest(),
        source_format=fmt,
        image=rgb,
    )

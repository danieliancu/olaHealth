"""Validation and re-encoding of every uploaded image (contributions and scan photos).

* Real type detected by Pillow from content, never from the name or the client MIME type.
* Only JPEG, PNG and WebP. SVG and anything else are rejected.
* Size, pixel count and minimum side limits; decompression bombs rejected.
* EXIF orientation applied, then all metadata (incl. GPS) dropped by re-encoding.
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

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


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


def validate_and_reencode(upload: UploadedFile | io.BytesIO) -> CleanImage:
    data = _read_limited(upload, settings.UPLOAD_MAX_BYTES)
    max_pixels = settings.UPLOAD_MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                if fmt not in ALLOWED_FORMATS:
                    raise ImageRejected(_("Only JPEG, PNG or WebP photos are accepted."))
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
        source_format=fmt or "",
        image=rgb,
    )

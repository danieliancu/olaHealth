"""Set the Magazine cover photo from a file, exactly as an upload in Staff → Magazine → Cover.

    python manage.py set_cover_photo "Smiling blonde woman with blood orange.png"

The file goes through the same checks and conversion as a staff upload (image only, size
limit, metadata stripped, WebP no wider than 3200 px), replaces the previous photo (its file is
deleted) and the change is written to the staff audit log. ``--fit`` also places the photo from
its shape: centred on the desktop cover, and in the phone card with the person on the right
(the topics sit on the left). A wide photo can be made with ``tools/widen_cover.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Set the Magazine cover photo from a file (same processing as a staff upload)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", help="Image file (PNG, JPEG or WebP)")
        parser.add_argument(
            "--fit", action="store_true", help="Reset the desktop position to the defaults"
        )

    def handle(self, *args, **options) -> None:
        from core.images import ImageRejected, upload_to_webp
        from core.models import MagazineCover
        from staff import services
        from staff.forms import COVER_MAX_WIDTH, MAX_IMAGE_BYTES

        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise CommandError("The image is larger than 15 MB.")
        try:
            data = upload_to_webp(io.BytesIO(path.read_bytes()), COVER_MAX_WIDTH, MAX_IMAGE_BYTES)
        except ImageRejected as exc:
            raise CommandError(str(exc)) from exc

        cover = MagazineCover.load()
        old = cover.image.name
        cover.image.save("cover.webp", ContentFile(data), save=False)
        if options["fit"]:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                ratio = img.width / img.height
            # Desktop: the photo the cover's height, centred. Phone card (3:2): the photo the
            # card's height, its middle at 68% of the card's width (the topics on the left).
            cover.photo_size, cover.photo_y = 100, 0
            cover.photo_x = -round(50 * ratio)
            cover.mobile_zoom, cover.mobile_y = 100, 0
            cover.mobile_x = max(-200, min(0, round(68 - ratio * 100 / 3)))
        cover.save()
        if old and old != cover.image.name:
            cover.image.storage.delete(old)  # replaced: no orphan file
        services.log(None, "magazine_cover_save", str(cover), photo=path.name, via="command")
        self.stdout.write(
            self.style.SUCCESS(
                f"Cover photo set ({len(data) // 1024} KB WebP); desktop position "
                f"{cover.photo_size}/{cover.photo_x}/{cover.photo_y}."
            )
        )

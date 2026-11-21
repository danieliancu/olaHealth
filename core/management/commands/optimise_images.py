"""Bring photos stored before the web pipeline in line with it (safe to run again):

* Learn article photos -> WebP, metadata stripped, at most LEARN_MAX_WIDTH wide.
* Old JPEG copies of Open Food Facts photos are deleted; they come back as small WebP on next view.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from core.images import LEARN_MAX_WIDTH, ImageRejected, decode, web_webp
from core.models import LearnArticle


class Command(BaseCommand):
    help = "Re-encode stored photos to web-sized WebP (Learn) and drop old OFF JPEG copies."

    def handle(self, *args, **options):
        done = 0
        for a in LearnArticle.objects.exclude(image=""):
            if a.image.name.endswith(".webp"):
                continue
            try:
                with a.image.open("rb") as f:
                    rgb, _fmt = decode(f.read())
            except (ImageRejected, OSError) as exc:
                self.stderr.write(f"{a.slug}: skipped ({exc})")
                continue
            old = a.image.name
            a.image.save(
                f"{a.slug[:60]}.webp", ContentFile(web_webp(rgb, LEARN_MAX_WIDTH)), save=True
            )
            default_storage.delete(old)
            done += 1
            self.stdout.write(f"{a.slug}: {old} -> {a.image.name}")
        removed = 0
        try:
            files = default_storage.listdir("offcache")[1]
        except (FileNotFoundError, NotImplementedError):
            files = []
        for name in files:
            if name.endswith(".jpg"):
                default_storage.delete(f"offcache/{name}")
                removed += 1
        self.stdout.write(f"Learn photos converted: {done}. Old OFF copies removed: {removed}.")

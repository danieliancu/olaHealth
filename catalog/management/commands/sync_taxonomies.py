"""Import the complete Open Food Facts category and country taxonomies.

They are reference data: the database knows every possible category and country, while
the catalogue filters only show the ones that currently have products. Run once at
install (``web`` does it automatically when the tables are empty) and periodically to
pick up new entries. Read-only download from a fixed OFF host.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Category, Country

SOURCES = {
    "categories": "https://static.openfoodfacts.org/data/taxonomies/categories.json",
    "countries": "https://static.openfoodfacts.org/data/taxonomies/countries.json",
}
MAX_BYTES = 40 * 1024 * 1024


def _download(url: str) -> dict[str, Any]:
    timeout = httpx.Timeout(120.0, connect=30.0)
    headers = {"User-Agent": settings.OFF_USER_AGENT}
    with (
        httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client,
        client.stream("GET", url) as response,
    ):
        if response.status_code != 200:
            raise CommandError(f"{url}: HTTP {response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise CommandError(f"{url}: file larger than expected")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise CommandError(f"{url}: unexpected format")
    return payload


def display_name(tag: str, entry: dict[str, Any]) -> str:
    names = entry.get("name") if isinstance(entry.get("name"), dict) else {}
    for lang in ("en", "xx"):
        if names.get(lang):
            return str(names[lang])[:300]
    if names:
        return str(next(iter(names.values())))[:300]
    text = tag.split(":", 1)[-1].replace("-", " ")
    return text[:1].upper() + text[1:]


def _first(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if isinstance(value, dict):
        value = value.get("en") or next(iter(value.values()), "")
    return str(value or "")


class Command(BaseCommand):
    help = "Import the full Open Food Facts category and country taxonomies."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--if-empty", action="store_true", help="only run when the tables are empty"
        )

    def handle(self, *args, if_empty: bool = False, **options) -> None:
        if if_empty and Category.objects.exists() and Country.objects.exists():
            self.stdout.write("Taxonomies already present.")
            return
        try:
            categories = _download(SOURCES["categories"])
            countries = _download(SOURCES["countries"])
        except (httpx.HTTPError, ValueError) as exc:
            # Never block startup: filters fall back to readable tag names.
            self.stderr.write(f"Taxonomy download failed: {type(exc).__name__}")
            return
        with transaction.atomic():
            Category.objects.bulk_create(
                [
                    Category(
                        tag=tag[:200],
                        name=display_name(tag, entry),
                        parents=[p for p in entry.get("parents", []) if isinstance(p, str)],
                    )
                    for tag, entry in categories.items()
                    if isinstance(entry, dict)
                ],
                batch_size=2000,
                update_conflicts=True,
                unique_fields=["tag"],
                update_fields=["name", "parents"],
            )
            Country.objects.bulk_create(
                [
                    Country(
                        tag=tag[:100],
                        name=display_name(tag, entry)[:200],
                        code=_first(entry, "country_code_2")[:3].upper(),
                    )
                    for tag, entry in countries.items()
                    if isinstance(entry, dict)
                ],
                batch_size=1000,
                update_conflicts=True,
                unique_fields=["tag"],
                update_fields=["name", "code"],
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{Category.objects.count()} categories, {Country.objects.count()} countries."
            )
        )

"""Count database queries for the main pages (uses whatever data is in the database)."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalog.models import Product


class Command(BaseCommand):
    help = "Print the number of SQL queries for the main pages."

    def handle(self, *args, **options) -> None:
        client = Client(HTTP_HOST="localhost")
        products = list(Product.objects.filter(current_revision__isnull=False)[:3])
        urls = [
            reverse("home"),
            reverse("scan"),
            reverse("products") + "?include_demo=on",
            reverse("products") + "?include_demo=on&q=yogurt&sort=nutriscore",
            reverse("saved"),
            reverse("sources"),
        ]
        if products:
            urls.append(reverse("product", args=[products[0].pk]))
        if len(products) >= 2:
            urls.append(reverse("compare") + "?ids=" + ",".join(str(p.pk) for p in products))
        for url in urls:
            with CaptureQueriesContext(connection) as ctx:
                status = client.get(url, secure=True).status_code
            self.stdout.write(f"{len(ctx.captured_queries):3d} queries  {status}  {url}")

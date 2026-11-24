"""The "My products" list: the products a user interacted with (``myola.models.MyProduct``).

A product enters the list when the user scans it, opens its page on purpose, saves it
(favourite, buy often, avoid) or adds it to the diary. Showing it in a search, an Ask Foodo
answer, alternatives or a Magazine article never does, and neither do imports or the OFF
sync (they never call ``touch``). Products hidden by the user stay out of the list.
"""

from __future__ import annotations

from django.db import connection
from django.utils import timezone

from myola.models import MyProduct, ProductMark

FLAG = {"scan": "scanned", "open": "opened", "saved": "saved", "eaten": "eaten"}


def touch(user, product, origin: str, when=None) -> None:
    """Record one interaction: one row per (user, product), the newest interaction wins
    ``last_at`` and ``last_origin``, the flags only ever turn on. One SQL statement
    (INSERT ... ON CONFLICT), so retries and concurrent requests never duplicate."""
    if user is None or not getattr(user, "is_authenticated", False) or product is None:
        return
    flag = FLAG[origin]
    when = when or timezone.now()
    table = MyProduct._meta.db_table
    flags = {name: name == flag for name in FLAG.values()}
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {table} (user_id, product_id, first_at, last_at, last_origin,
                                 scanned, opened, saved, eaten)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, product_id) DO UPDATE SET
              first_at = LEAST({table}.first_at, EXCLUDED.first_at),
              last_origin = CASE WHEN EXCLUDED.last_at >= {table}.last_at
                                 THEN EXCLUDED.last_origin ELSE {table}.last_origin END,
              last_at = GREATEST({table}.last_at, EXCLUDED.last_at),
              scanned = {table}.scanned OR EXCLUDED.scanned,
              opened = {table}.opened OR EXCLUDED.opened,
              saved = {table}.saved OR EXCLUDED.saved,
              eaten = {table}.eaten OR EXCLUDED.eaten
            """,  # noqa: S608 - the table name is the model's own
            [user.pk, product.pk, when, when, origin, flags["scanned"], flags["opened"],
             flags["saved"], flags["eaten"]],
        )  # fmt: skip


def listing(user):
    """The user's products, newest interaction first, without the ones they hid."""
    hidden = ProductMark.objects.filter(user=user, kind=ProductMark.Kind.HIDDEN).values(
        "product_id"
    )
    return (
        MyProduct.objects.filter(user=user, product__current_revision__isnull=False)
        .exclude(product_id__in=hidden)
        .select_related("product", "product__current_revision")
        .order_by("-last_at", "-pk")
    )


def forget_history(user) -> int:
    """With "Clear history": drop the products only scanned or opened (saved ones and those
    in the diary stay)."""
    return MyProduct.objects.filter(user=user, saved=False, eaten=False).delete()[0]

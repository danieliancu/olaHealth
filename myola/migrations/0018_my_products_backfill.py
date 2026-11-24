"""Fill "My products" for existing accounts from what they already did: visits (a visit with a
barcode is a scan, else an open), saved marks (not "hidden") and diary entries with a product.

The same upsert as ``myola.services.my_products.touch``: running it again changes nothing
(one row per user and product, flags only turn on, the newest interaction wins).
"""

from django.db import migrations

BACKFILL = """
WITH acts AS (
    SELECT user_id, product_id, created_at AS at,
           CASE WHEN code <> '' THEN 'scan' ELSE 'open' END AS origin
      FROM myola_productvisit
    UNION ALL
    SELECT user_id, product_id, created_at, 'saved' FROM myola_productmark WHERE kind <> 'hidden'
    UNION ALL
    SELECT user_id, product_id, created_at, 'eaten'
      FROM myola_diaryentry WHERE product_id IS NOT NULL
), agg AS (
    SELECT user_id, product_id, MIN(at) AS first_at, MAX(at) AS last_at,
           (ARRAY_AGG(origin ORDER BY at DESC))[1] AS last_origin,
           BOOL_OR(origin = 'scan') AS scanned, BOOL_OR(origin = 'open') AS opened,
           BOOL_OR(origin = 'saved') AS saved, BOOL_OR(origin = 'eaten') AS eaten
      FROM acts GROUP BY user_id, product_id
)
INSERT INTO myola_myproduct (user_id, product_id, first_at, last_at, last_origin,
                             scanned, opened, saved, eaten)
SELECT user_id, product_id, first_at, last_at, last_origin, scanned, opened, saved, eaten
  FROM agg
ON CONFLICT (user_id, product_id) DO UPDATE SET
  first_at = LEAST(myola_myproduct.first_at, EXCLUDED.first_at),
  last_origin = CASE WHEN EXCLUDED.last_at >= myola_myproduct.last_at
                     THEN EXCLUDED.last_origin ELSE myola_myproduct.last_origin END,
  last_at = GREATEST(myola_myproduct.last_at, EXCLUDED.last_at),
  scanned = myola_myproduct.scanned OR EXCLUDED.scanned,
  opened = myola_myproduct.opened OR EXCLUDED.opened,
  saved = myola_myproduct.saved OR EXCLUDED.saved,
  eaten = myola_myproduct.eaten OR EXCLUDED.eaten;
"""


class Migration(migrations.Migration):
    dependencies = [("myola", "0017_my_products")]

    operations = [migrations.RunSQL(BACKFILL, reverse_sql=migrations.RunSQL.noop)]

"""Pilot of the local Open Food Facts catalogue: sample, import, measure, compare.

Not part of the app; a measuring tool for docs/off-local-catalogue.md. Outputs go to
``data/openfoodfacts/pilot/`` (git-ignored).

    # 1. one pass over the dump (host or container, no Django needed)
    python tools/off_pilot.py sample data/openfoodfacts/openfoodfacts-products.jsonl.gz

    # 2. import the sample into a fresh database and measure (inside the web container)
    docker compose -f compose.yaml -f compose.dev.yaml run --rm -e POSTGRES_DB=pilot_before \
        web python tools/off_pilot.py run --db pilot_before

    # 3. compare two runs (no Django needed)
    python tools/off_pilot.py compare pilot_before pilot_after

    # 4. check a sample of photo URLs over HTTP (about one request per second)
    python tools/off_pilot.py check-urls pilot_after

``run`` creates the database named by ``--db`` (it must equal POSTGRES_DB) and refuses to
touch the main database.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "data" / "openfoodfacts" / "pilot"
SAMPLE = PILOT / "sample.jsonl.gz"
STRATA = PILOT / "sample_strata.json"
CENSUS = PILOT / "census.json"

ZONE_LINES = 250_000
ZONE_KEEP = 1_000
RO_KEEP = 3_000
UK_KEEP = 2_000
BEVERAGE_KEEP = 1_000
INCOMPLETE_KEEP = 1_000
SEED = 20261123


# --------------------------------------------------------------------------- sample


class Reservoir:
    def __init__(self, size: int, rnd: random.Random) -> None:
        self.size, self.rnd, self.seen, self.items = size, rnd, 0, []

    def offer(self, item) -> None:
        self.seen += 1
        if len(self.items) < self.size:
            self.items.append(item)
        else:
            j = self.rnd.randrange(self.seen)
            if j < self.size:
                self.items[j] = item


def _incomplete(p: dict) -> bool:
    nutriments = p.get("nutriments") if isinstance(p.get("nutriments"), dict) else {}
    has_per_100 = any(
        nutriments.get(k) not in (None, "")
        for k in ("energy-kcal_100g", "energy-kj_100g", "energy_100g", "fat_100g", "salt_100g")
    )
    return not str(p.get("product_name") or "").strip() or not has_per_100


def cmd_sample(args) -> None:
    PILOT.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(SEED)
    zones: dict[int, Reservoir] = {}
    ro = Reservoir(RO_KEEP, rnd)
    uk = Reservoir(UK_KEEP, rnd)
    bev = Reservoir(BEVERAGE_KEEP, rnd)
    inc = Reservoir(INCOMPLETE_KEEP, rnd)
    census = Counter()
    sizes = Counter()
    t0 = time.time()
    proc = subprocess.Popen(["gzip", "-dc", args.dump], stdout=subprocess.PIPE, bufsize=1 << 24)
    assert proc.stdout is not None
    for n, line in enumerate(proc.stdout):
        if not line.strip():
            continue
        census["lines"] += 1
        census["bytes"] += len(line)
        zone = n // ZONE_LINES
        zones.setdefault(zone, Reservoir(ZONE_KEEP, rnd)).offer(line)
        # Cheap substring signals, counted on every line (labelled as such in the census).
        has_kcal = b'"energy-kcal_100g"' in line or b'"energy_100g"' in line
        has_name = b'"product_name":"' in line and b'"product_name":""' not in line
        census["sig_name"] += has_name
        census["sig_per100_energy"] += has_kcal
        census["sig_name_and_per100"] += has_name and has_kcal
        census["sig_images_object"] += b'"images":{' in line
        census["sig_images_selected"] += b'"selected":{' in line
        census["sig_images_old_front"] += b'"front_' in line and b'"imgid"' in line
        if b'"en:romania"' in line or b'"en:united-kingdom"' in line or b'"en:beverages"' in line:
            p = json.loads(line)
            countries = p.get("countries_tags") or []
            cats = p.get("categories_tags") or []
            if "en:romania" in countries:
                census["countries_ro"] += 1
                ro.offer(line)
            if "en:united-kingdom" in countries:
                census["countries_uk"] += 1
                uk.offer(line)
            if "en:beverages" in cats:
                census["beverages"] += 1
                bev.offer(line)
        if not has_name or not has_kcal:
            inc.offer(line)
        if census["lines"] % 500_000 == 0:
            print(f"{census['lines']} lines, {time.time() - t0:.0f}s", flush=True)
    proc.wait()
    census["seconds"] = round(time.time() - t0)

    chosen: dict[str, bytes] = {}
    strata: dict[str, list[str]] = {}

    def take(lines, name, check=None):
        for line in lines:
            p = json.loads(line)
            if check is not None and not check(p):
                continue
            code = str(p.get("code") or "")
            if not code:
                continue
            chosen.setdefault(code, line)
            strata.setdefault(code, [])
            if name not in strata[code]:
                strata[code].append(name)

    for zone, res in sorted(zones.items()):
        take(res.items, f"zone{zone:02d}")
    take(ro.items, "romania")
    take(uk.items, "uk")
    take(bev.items, "beverage")
    take(inc.items, "incomplete", _incomplete)
    for line in chosen.values():
        sizes["sample_bytes"] += len(line)
    with gzip.open(SAMPLE, "wb") as fh:
        for code in sorted(chosen):
            fh.write(chosen[code].rstrip(b"\n") + b"\n")
    STRATA.write_text(json.dumps(strata), encoding="utf-8")
    by_stratum = Counter(s for v in strata.values() for s in v)
    census.update(
        {
            "sample_products": len(chosen),
            "sample_avg_line_bytes": round(sizes["sample_bytes"] / max(1, len(chosen))),
            "avg_line_bytes": round(census["bytes"] / max(1, census["lines"])),
            "zones": len(zones),
        }
    )
    out = {"census": dict(census), "sample_by_stratum": dict(sorted(by_stratum.items()))}
    CENSUS.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


# --------------------------------------------------------------------------- run


def _django():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "olahealth.settings")
    import django

    django.setup()


def _create_database(name: str) -> None:
    import psycopg
    from django.conf import settings

    db = settings.DATABASES["default"]
    if db["NAME"] != name:
        sys.exit(f"POSTGRES_DB is {db['NAME']!r}; run with -e POSTGRES_DB={name}")
    if name in ("olahealth", os.environ.get("POSTGRES_MAIN_DB", "olahealth")):
        sys.exit("Refusing to use the main database.")
    conn = psycopg.connect(
        dbname="postgres",
        user=db["USER"],
        password=db["PASSWORD"],
        host=db["HOST"],
        port=db["PORT"],
        autocommit=True,
    )
    with conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')


def _sizes() -> dict:
    from django.db import connection

    tables = [
        "catalog_product",
        "catalog_productrevision",
        "catalog_nutrientfact",
        "assessments_assessmentsnapshot",
        "catalog_productimage",
        "catalog_productidentifier",
    ]
    out = {}
    with connection.cursor() as c:
        for t in tables:
            c.execute(
                "select pg_relation_size(%s), pg_indexes_size(%s), pg_total_relation_size(%s)",
                [t, t, t],
            )
            heap, idx, total = c.fetchone()
            c.execute(f"select count(*) from {t}")
            out[t] = {
                "rows": c.fetchone()[0],
                "heap_and_toast": total - idx,
                "indexes": idx,
                "heap_main": heap,
                "total": total,
            }
        c.execute("select pg_database_size(current_database())")
        out["database_total"] = c.fetchone()[0]
    return out


def _payload_stats() -> dict:
    from django.db import connection

    with connection.cursor() as c:
        c.execute(
            "select count(*), avg(pg_column_size(off_payload)),"
            " avg(octet_length(off_payload::text)),"
            " max(octet_length(off_payload::text)), avg(pg_column_size(data))"
            " from catalog_productrevision where off_payload is not null"
        )
        n, stored, text, text_max, data = c.fetchone()
    return {
        "revisions_with_payload": n,
        "avg_payload_stored_bytes": round(float(stored or 0)),
        "avg_payload_json_bytes": round(float(text or 0)),
        "max_payload_json_bytes": text_max,
        "avg_data_stored_bytes": round(float(data or 0)),
    }


def _image_stats(strata: dict[str, list[str]]) -> dict:
    from catalog.models import Product, ProductImage

    active = ProductImage.objects.filter(is_active=True, source="off")
    by_kind = {}
    for kind in ("front", "ingredients", "nutrition"):
        by_kind[kind] = active.filter(kind=kind).values("product").distinct().count()
    with_any = active.values("product").distinct().count()
    per_stratum = Counter()
    totals = Counter()
    codes_with_front = set(
        Product.objects.filter(images__in=active.filter(kind="front")).values_list(
            "off_code", flat=True
        )
    )
    for code, names in strata.items():
        for s in names:
            key = "zones" if s.startswith("zone") else s
            totals[key] += 1
            per_stratum[key] += code in codes_with_front
    return {
        "products_with_any_off_photo": with_any,
        "products_with_photo_by_kind": by_kind,
        "front_photo_by_stratum": {k: f"{per_stratum[k]}/{totals[k]}" for k in sorted(totals)},
    }


STARTED = time.time()


def _queue_and_cache() -> dict:
    """Queued background tasks in this database, and photo cache files written since start
    (the private volume is shared with the main app, so only new files count)."""
    from django.db import connection

    with connection.cursor() as c:
        c.execute("select count(*) from django_q_ormq")
        queued = c.fetchone()[0]
    root = Path(os.environ.get("PRIVATE_MEDIA_ROOT", "/data/private")) / "offcache"
    new_files = (
        sum(1 for f in root.glob("*") if f.stat().st_mtime >= STARTED) if root.exists() else 0
    )
    return {"queued_tasks": queued, "offcache_files_written": new_files}


def _snapshot(path: Path) -> None:
    """One line per product: what must not change between runs."""
    from assessments.score import core
    from catalog.models import Product

    with path.open("w", encoding="utf-8") as fh:
        qs = Product.objects.select_related("current_revision").order_by("off_code")
        for p in qs.iterator(chunk_size=500):
            r = p.current_revision
            data = dict(r.data) if r else {}
            fh.write(
                json.dumps(
                    {
                        "code": p.off_code,
                        "data_hash": hashlib.sha256(
                            json.dumps(data, sort_keys=True).encode()
                        ).hexdigest(),
                        "facts": (data.get("nutrition") or {}).get("facts"),
                        "foodo": core(r.foodo_score or {}) if r else None,
                        "foodo_value": p.foodo_value,
                        "grade": p.nutriscore_grade,
                        "nova": p.nova_group,
                        "revisions": p.revisions.count(),
                        "identifiers": p.identifiers.count(),
                        "images": sorted(
                            [i.kind, i.remote_url]
                            for i in p.images.filter(is_active=True, source="off")
                        ),
                        "payload_bytes": len(json.dumps(r.off_payload))
                        if r and r.off_payload
                        else 0,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def _import(path: Path, verify: bool = True) -> dict:
    from catalog.services.catalog_import import run_jsonl

    t0 = time.time()
    run = run_jsonl(path, verify_after=verify)
    seconds = time.time() - t0
    checks = {c["check"]: c["ok"] for c in (run.report or {}).get("checks", [])}
    return {
        "status": run.status,
        "seen": run.seen,
        "imported": run.imported,
        "skipped": run.skipped,
        "failed": run.failed,
        "seconds": round(seconds, 1),
        "per_second": round(run.seen / seconds, 1) if seconds else None,
        "checks": checks,
        "errors": (run.report or {}).get("errors", [])[:5],
    }


def _counts() -> dict:
    from django.db.models import Count

    from catalog.models import Product, ProductIdentifier, ProductRevision, ReviewFlag

    dup_codes = (
        Product.objects.exclude(off_code=None)
        .values("off_code")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .count()
    )
    dup_idents = (
        ProductIdentifier.objects.values("normalized", "context")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .count()
    )
    return {
        "products": Product.objects.count(),
        "revisions": ProductRevision.objects.count(),
        "identifiers": ProductIdentifier.objects.count(),
        "duplicate_off_codes": dup_codes,
        "duplicate_identifiers": dup_idents,
        "open_flags": ReviewFlag.objects.filter(status="open").count(),
    }


def _correction_scenario(tmp: Path) -> dict:
    """Approve a local correction on 3 products, then import a newer OFF line for each."""
    import copy

    from django.contrib.auth import get_user_model

    from catalog.models import Product, ReviewFlag
    from contributions.models import Contribution
    from contributions.services.moderation import approve

    moderator, _ = get_user_model().objects.get_or_create(
        username="pilot-moderator", defaults={"is_staff": True, "is_superuser": True}
    )
    from contributions.services.moderation import ModerationError

    results = []
    lines = []
    skipped = []
    for p in Product.objects.select_related("current_revision").order_by("off_code"):
        if len(results) == 3:
            break
        facts = (p.data.get("nutrition") or {}).get("facts") or []
        per100 = [f for f in facts if f["basis"] != "per_serving" and f["preparation"] == "as_sold"]
        if not p.data.get("name") or not any(f["nutrient"] == "sugars" for f in per100):
            continue
        rev = p.current_revision
        nutrition = copy.deepcopy(rev.data["nutrition"])
        for f in nutrition["facts"]:
            if f["nutrient"] == "sugars" and f["basis"] != "per_serving":
                f["value"] = str(round(float(f["value"]) + 1, 3))
        local_name = f"{rev.data['name']} (local)"
        c = Contribution.objects.create(
            product=p,
            base_revision=rev,
            problem_type=Contribution.ProblemType.INCORRECT_DATA,
            proposed={"name": local_name, "nutrition": nutrition},
            status=Contribution.Status.SUBMITTED,
            consent_publication=True,
        )
        try:
            outcome = approve(c.pk, moderator, "pilot")
        except ModerationError as exc:
            skipped.append([p.off_code, str(exc)[:120]])
            continue
        if outcome.status != "approved":
            skipped.append([p.off_code, outcome.status])
            continue
        p.refresh_from_db()
        revisions_after_approval = p.revisions.count()
        payload = copy.deepcopy(rev.off_payload)
        payload["product_name"] = f"{payload.get('product_name') or ''} NEW FROM OFF"
        payload["last_modified_t"] = int(payload.get("last_modified_t") or 0) + 86400
        lines.append(json.dumps(payload))
        results.append((p.pk, local_name, nutrition["facts"], revisions_after_approval))
    path = tmp / "correction.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    imported = _import(path, verify=False)
    checks = []
    for pk, local_name, facts, revs in results:
        p = Product.objects.select_related("current_revision").get(pk=pk)
        checks.append(
            {
                "code": p.off_code,
                "name_kept": p.current_revision.data["name"] == local_name,
                "nutrition_kept": p.current_revision.data["nutrition"]["facts"] == facts,
                "locked": sorted(p.locked_fields),
                "revisions_before_after": [revs, p.revisions.count()],
                "off_name_in_source": "NEW FROM OFF"
                in (p.current_revision.off_data or {}).get("name", ""),
                "data_conflict_flag": ReviewFlag.objects.filter(
                    product=p, kind="data_conflict", field="name", status="open"
                ).exists(),
                "front_photo_active": p.images.filter(is_active=True, kind="front").exists(),
            }
        )
    return {"import": imported, "products": checks, "skipped_candidates": skipped}


def cmd_run(args) -> None:
    import tempfile

    _django()
    _create_database(args.db)
    from django.core.management import call_command
    from django.db import connection

    connection.close()
    call_command("migrate", verbosity=0)
    strata = json.loads(STRATA.read_text(encoding="utf-8"))
    PILOT.mkdir(parents=True, exist_ok=True)
    # run_jsonl hashes and reads the file; give it a local copy of the sample.
    tmp = Path(tempfile.mkdtemp())
    local = tmp / "sample.jsonl.gz"
    local.write_bytes(SAMPLE.read_bytes())
    result = {"db": args.db, "git": _git()}
    result["first_import"] = _import(local)
    result["after_first"] = {
        **_counts(),
        **_payload_stats(),
        "sizes": _sizes(),
        **_image_stats(strata),
        **_queue_and_cache(),
    }
    _snapshot(PILOT / f"{args.db}_products.jsonl")
    _dump_size(result)
    result["reimport_same_file"] = _import(local, verify=False)
    result["after_reimport"] = _counts()
    result["correction"] = _correction_scenario(tmp)
    result["after_correction"] = {**_counts(), **_queue_and_cache()}
    out = PILOT / f"{args.db}.json"
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps(result, indent=1, default=str))


def _git() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip() + (" (dirty)" if _dirty() else "")
    except OSError:
        return "unknown"


def _dirty() -> bool:
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "status",
                "--porcelain",
                "--",
                "off",
                "catalog",
                "assessments",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return bool(out.strip())
    except OSError:
        return False


def _dump_size(result: dict) -> None:
    """Size of a custom-format pg_dump of the pilot database (backup estimate)."""
    from django.conf import settings

    db = settings.DATABASES["default"]
    env = {**os.environ, "PGPASSWORD": db["PASSWORD"]}
    try:
        proc = subprocess.run(
            ["pg_dump", "-Fc", "-h", db["HOST"], "-U", db["USER"], "-d", db["NAME"]],
            capture_output=True,
            env=env,
            check=True,
        )
        result["pg_dump_custom_bytes"] = len(proc.stdout)
    except (OSError, subprocess.CalledProcessError) as exc:
        result["pg_dump_custom_bytes"] = f"unavailable: {exc}"


# --------------------------------------------------------------------------- compare


def _load(db: str) -> dict:
    rows = {}
    with (PILOT / f"{db}_products.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            rows[r["code"]] = r
    return rows


def cmd_compare(args) -> None:
    a, b = _load(args.before), _load(args.after)
    diff = Counter()
    examples: dict[str, list] = {}
    for code in sorted(set(a) | set(b)):
        if code not in a or code not in b:
            diff["missing_in_one"] += 1
            continue
        gained = not a[code]["facts"] and bool(b[code]["facts"])
        if gained:
            # Nutrition newly read from the current schema (``nutrition.input_sets``): the
            # product data differs on purpose; everything else must still match.
            diff["facts_gained"] += 1
        for key in (
            *(() if gained else ("data_hash", "facts")),
            "foodo",
            "foodo_value",
            "grade",
            "nova",
            "revisions",
            "identifiers",
            "images",
        ):
            if a[code][key] != b[code][key]:
                diff[key] += 1
                examples.setdefault(key, [])
                if len(examples[key]) < 3:
                    examples[key].append(code)
    pb = sum(r["payload_bytes"] for r in a.values()) / max(1, len(a))
    pa = sum(r["payload_bytes"] for r in b.values()) / max(1, len(b))
    out = {
        "products": [len(a), len(b)],
        "differences": dict(diff),
        "examples": examples,
        "with_photos": [
            sum(bool(r["images"]) for r in a.values()),
            sum(bool(r["images"]) for r in b.values()),
        ],
        "avg_payload_json_bytes": [round(pb), round(pa)],
    }
    print(json.dumps(out, indent=1))


# --------------------------------------------------------------------------- check-urls


def cmd_check_urls(args) -> None:
    import urllib.request

    rows = _load(args.db)
    strata = json.loads(STRATA.read_text(encoding="utf-8"))
    rnd = random.Random(SEED)
    buckets: dict[tuple, list] = {}
    for code, r in rows.items():
        length = "ean8" if len(code) <= 8 else ("upc12" if len(code) == 12 else "ean13+")
        for kind, url in r["images"]:
            buckets.setdefault((kind, length), []).append((code, url))
    picked = []
    for key in sorted(buckets):
        items = buckets[key]
        rnd.shuffle(items)
        picked += [(key, *x) for x in items[: args.per_bucket]]
    results = Counter()
    failures = []
    for (kind, length), code, url in picked:
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "olaHealth-pilot/0.1 (catalogue test)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                ok = resp.status == 200 and resp.headers.get("content-type", "").startswith(
                    "image/"
                )
        except Exception as exc:
            ok = False
            failures.append([code, url, str(exc)[:80]])
        results[f"{kind}/{length}/{'ok' if ok else 'fail'}"] += 1
        time.sleep(1)
    out = {
        "checked": len(picked),
        "results": dict(sorted(results.items())),
        "failures": failures[:20],
        "strata_known": len(strata),
    }
    (PILOT / f"{args.db}_urls.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("dump")
    r = sub.add_parser("run")
    r.add_argument("--db", required=True)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    u = sub.add_parser("check-urls")
    u.add_argument("db")
    u.add_argument("--per-bucket", type=int, default=25)
    args = parser.parse_args()
    {"sample": cmd_sample, "run": cmd_run, "compare": cmd_compare, "check-urls": cmd_check_urls}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()

# olaHealth

Mobile-first web app to scan, identify, compare and understand packaged foods.
Product data comes from [Open Food Facts](https://world.openfoodfacts.org) (read-only) and
from moderated corrections by anonymous contributors.

Stack: Python 3.13 · Django 5.2 LTS · PostgreSQL 17 · Django templates + small JS modules ·
Django REST Framework · Django Admin (moderation) · Django-Q2 (PostgreSQL ORM broker) ·
Pillow · Tesseract 5 (eng+ron) via pytesseract · zxing-cpp (server) · zxing-wasm (browser) ·
httpx · uv · Docker Compose (`web`, `worker`, `postgres`).

More: [docs/architecture.md](docs/architecture.md) ·
[docs/assessment-rules.md](docs/assessment-rules.md) ·
[docs/measurements.md](docs/measurements.md)

## Quick start (development)

Requirements: Docker with Compose v2. Nothing else on the host (Python, Tesseract and
PostgreSQL run in containers).

```sh
cp .env.example .env
# edit .env: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, OFF_USER_AGENT (with a real contact),
# and for local HTTP: DJANGO_DEBUG=1, SECURE_SSL_REDIRECT=0, SECURE_COOKIES=0

docker compose -f compose.yaml -f compose.dev.yaml up --build -d
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py createsuperuser
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py setup_roles
```

Open <http://localhost:8000>. The camera works on `localhost` (secure context); on any other
host it needs HTTPS. Admin / moderation: <http://localhost:8000/admin/>.

`web` runs migrations, creates the cache table, (re)creates the scheduled jobs and, on the
first start, imports the full Open Food Facts category and country taxonomies.

## Production-like run

```sh
cp .env.example .env    # keep DJANGO_DEBUG=0, set DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py setup_roles
```

Put a TLS-terminating reverse proxy in front of `web:8000` (it must set
`X-Forwarded-Proto`). Static files are served by WhiteNoise. Private uploads live in the
`private_media` volume and are only served through authorised views. Nothing is deployed
by this repository.

## MVP: onboarding, food diary and progress

* `/` → **Get started** (`/start/`): four short steps (goal, about you, restrictions and
  allergies, health conditions), then **Today** (`/today/`).
* **Today**: daily summary, food diary (breakfast, lunch, dinner, snacks), water and activity.
  Scan a barcode (Open Food Facts lookup), check the label, then **Add to diary** with the meal
  and the amount, with a preview before saving.
* **Progress** (`/progress/`): 7- and 30-day trends; optional weight.
* **My Track** (`/my-ola/`): profile and settings, scans and lists, Notes, Ask Foodo, and deleting
  your data. Search and comparison are in the header.
* No account: everything personal stays in this browser (`olahealth:myola`). Data contracts are
  in `docs/mvp-api-contracts.md`.

## Admin and roles

* `createsuperuser` – full access.
* `setup_roles` creates two groups:
  * **Reviewers** – can view contributions, photos, OCR text, revisions and flags.
  * **Moderators** – the above plus `moderate_contribution` (approve / reject / re-base).
  Add staff users (`is_staff`) to one of them in the admin.

Moderation: *Admin → Contributions → Review*. The review page shows the base / current /
proposed values side by side, photos, OCR output and parser proposals, validation
warnings and the audit log. Approve, reject (reason required) or re-base after a conflict
(reason required). Every action is recorded in *Moderation audits*.

## Commands

| Command | Purpose |
|---|---|
| `python manage.py setup_schedules` | create/update periodic jobs (idempotent, run by `web` at start) |
| `python manage.py setup_roles` | create the Reviewers / Moderators groups |
| `python manage.py sync_taxonomies [--if-empty]` | import/update the full OFF category and country lists |
| `python manage.py export_catalogue [--dir D]` | write the ODbL export (gzipped JSON Lines) |
| `python manage.py off_smoke [codes…] [--fresh]` | limited real OFF test (≤ 5 codes), timings |
| `python manage.py ocr_benchmark [images…] [--runs N]` | OCR latency and peak memory |
| `python manage.py measure_queries` | SQL queries per main page |

Run them with `docker compose -f compose.yaml -f compose.dev.yaml exec web …`.

## Scheduled jobs (Django-Q2, run by `worker`)

| Name | Schedule | Function |
|---|---|---|
| refresh-stale-products | hourly | re-import up to `OFF_REFRESH_BATCH` products older than `OFF_FRESH_TTL_HOURS` (or whose last refresh failed); stops at the shared rate limit |
| purge-coordination | hourly | expired leases, old rate-limit windows, old negative-cache rows |
| purge-expired-drafts | daily | drafts idle > `DRAFT_RETENTION_DAYS` (30) and photos of contributions rejected > `REJECTED_IMAGE_RETENTION_DAYS` (30) ago, with their files |
| delete-orphan-files | daily | private files no database row refers to (older than 1 h) |
| report-waiting-contributions | daily | logs contributions waiting > 7 days |
| export-catalogue | daily | regenerates `/sources/export/` (keeps the last 7 files) |
| sync-taxonomies | weekly | refreshes the category and country reference tables |

OCR jobs are queued on demand (one per photo of the ingredients / nutrition label).

## Checks

```sh
DC="docker compose -f compose.yaml -f compose.dev.yaml"
$DC run --rm --no-deps web ruff check .
$DC run --rm --no-deps web ruff format --check .
$DC run --rm --no-deps -e TESTING=1 web pytest                 # OFF mocked
$DC run --rm --no-deps -e TESTING=1 web pytest -m ocr -s       # real Tesseract on fixtures
$DC run --rm --no-deps web python manage.py makemigrations --check --dry-run
$DC run --rm --no-deps web python manage.py check
docker compose run --rm --no-deps -e DJANGO_DEBUG=0 web python manage.py check --deploy
```

The OCR fixtures in `tests/fixtures/ocr/` are synthetic labels we generate
(`python tests/fixtures/ocr/make_fixtures.py`), so there are no third-party rights.

## Your own artwork (optional)

The app ships original SVG illustrations. To use your own pictures instead, add raster
files **with the same name** next to the SVGs; they are picked up automatically (`.webp`,
then `.png`, then `.jpg`):

| File | Shows | Suggested size |
|---|---|---|
| `static/img/hero.webp` | home hero (cup, oats, apple), light/transparent background | 1280×640 |
| `static/img/placeholder.webp` | "no photo yet" placeholder | 400×400 |

Use only images you have the rights to, without real brands or logos. In production,
rebuild the image (`docker compose build`) so `collectstatic` includes them.

## Licences

* Data from Open Food Facts: Open Database License (ODbL); contents: Database Contents
  License. Product photos from OFF: CC BY-SA, credited to OFF contributors.
* Contributed photos: published only after moderation and only with the contributor's
  explicit consent (CC BY-SA 4.0 for photos, ODbL for data; see the consent text).
* Bundled third-party code: zxing-wasm (MIT, `static/vendor/zxing-wasm/LICENSE`),
  Lucide icons (ISC, `static/vendor/lucide/LICENSE`), Chart.js 4.6.1 (MIT,
  `static/vendor/chartjs/LICENSE.md`, loaded only on the Compare page).
* The licence of the olaHealth **code** has not been chosen yet; it is independent of
  the data licence. Add a `LICENSE` file when the owner decides.

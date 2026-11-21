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

* `/` → **Get started**: sign up with email + password (confirmation email; in development
  the inbox is Mailpit at <http://localhost:18025>), then four short steps (`/start/`: goal,
  about you, restrictions and allergies, health conditions with explicit consent), then
  **Today** (`/today/`). Scanning, search, product pages and comparison work without an account.
* **Today**: daily summary, food diary (breakfast, lunch, dinner, snacks), water and activity.
  Scan a barcode (Open Food Facts lookup), check the label, then **Add to diary** with the meal
  and the amount, with a preview before saving.
* **Progress** (`/progress/`): 7- and 30-day trends; optional weight.
* **My Track** (`/ask-foodo/`): profile and settings, scans and lists, Notes, Ask Foodo, and deleting
  your data. Search and comparison are in the header.
* Personal data is stored in PostgreSQL per account (`myola/models.py`) and deleted with the
  account; **Download my data** exports it as JSON. Pages are server-rendered Django + htmx;
  the only JavaScript left is the camera scanner and the confirm dialog.
* **Foodo Score** (0–100, `assessments/score.py`, rule in `docs/assessment-rules.md`) and up to
  three better alternatives sold in the UK on each product page.
* **Ask Foodo** answers from Python-computed facts; when enabled in the staff panel an AI model
  (Claude Haiku 5.5 by default, or OpenAI) only rephrases them, within per-user daily and
  monthly budget limits. API keys: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
* **Staff panel** (`/staff/`): dashboard, moderation queue, review flags, products, users,
  site settings and audit log. **API docs**: `/api/v1/docs/` (OpenAPI at `/api/v1/schema/`).

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

CI (`.github/workflows/ci.yml`) runs lint, format, the migrations check, `check --deploy` and
the test suite on every pull request and on pushes to `main`.

## Backups

The `backup` service writes, every day at `BACKUP_HOUR` (UTC, default 3), a PostgreSQL dump
(`db-<timestamp>.dump`, custom format) and an archive of the private media
(`media-<timestamp>.tar.gz`, contributed photos) into the `backups` volume, and deletes files
older than `BACKUP_KEEP_DAYS` (default 14). Copy that volume off the server regularly
(the host's own backup or `docker run --rm -v olahealth_backups:/b alpine tar ...`).

```sh
docker compose run --rm backup sh /scripts/backup.sh          # backup now
docker compose run --rm backup ls -la /backups                # list backups
# Restore (drops and recreates the database objects):
docker compose stop web worker
docker compose run --rm backup sh /scripts/restore.sh db-<ts>.dump media-<ts>.tar.gz
docker compose start web worker
```

Restore check (does not touch the live database): restore the latest dump into a scratch
database and compare a row count, e.g. `createdb restore_check && pg_restore -d restore_check
... && psql -d restore_check -c "select count(*) from catalog_product"`, then `dropdb`.

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
  Lucide icons (ISC, `static/vendor/lucide/LICENSE`), htmx 2.0.4 (0BSD,
  `static/vendor/htmx/LICENSE`). The Compare charts are SVG drawn on
  the server (no chart library).
* The AskFoodo **code** is proprietary, all rights reserved (see `LICENSE`). This is
  independent of the data licences above.

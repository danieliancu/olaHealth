# One image for web and worker.
FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Tesseract + the language data we document (English, Romanian) are installed
# at build time: no model downloads happen at request time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-ron \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

WORKDIR /app
ARG INSTALL_DEV=false
COPY pyproject.toml uv.lock ./
RUN if [ "$INSTALL_DEV" = "true" ]; then uv sync --frozen; else uv sync --frozen --no-dev; fi

COPY . .
RUN DJANGO_SECRET_KEY=build-only-not-secret python manage.py collectstatic --noinput \
    && useradd --system --uid 1000 app \
    && mkdir -p /data/private /data/exports \
    && chown -R app /data

USER app
EXPOSE 8000
CMD ["gunicorn", "olahealth.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30"]

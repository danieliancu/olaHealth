"""Django settings for olaHealth. Every deploy-specific value comes from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Missing environment variable {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",  # dev: WhiteNoise serves static with max-age=0
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "django_q",
    "allauth",
    "allauth.account",
    "core",
    "catalog",
    "assessments",
    "contributions",
    "myola",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "olahealth.urls"
WSGI_APPLICATION = "olahealth.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.site",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "olahealth"),
        "USER": env("POSTGRES_USER", "olahealth"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        # Separate test databases let several test runs share one Postgres server.
        "TEST": {"NAME": env("POSTGRES_TEST_DB", "") or None},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The database cache is used only for incidental caching; correctness-critical
# coordination (leases, rate limits) uses explicit tables in core/.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Accounts (django-allauth): email + password, email confirmation required ---------
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",  # staff login to /admin/
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"  # filled automatically from the email
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", "mandatory")
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = "FoodoTrack · "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
# allauth's own limits (kept in the cache): login failures, signups, resets, emails.
ACCOUNT_RATE_LIMITS = {
    "login": "30/m/ip",
    "login_failed": "10/m/ip,5/5m/key",
    "signup": "10/h/ip",
    "confirm_email": "1/3m/key",
    "reset_password": "20/m/ip,5/m/key",
    "reset_password_from_key": "20/m/ip",
    "change_password": "5/m/user",
    "manage_email": "10/m/user",
}
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "today"
ACCOUNT_SIGNUP_REDIRECT_URL = "start"
ACCOUNT_LOGOUT_REDIRECT_URL = "home"

# Email -------------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "FoodoTrack <no-reply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

LANGUAGE_CODE = "en"
LANGUAGES = [("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    # Private uploads: never under a public URL, served only via authorised views.
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": env("PRIVATE_MEDIA_ROOT", "/data/private"), "base_url": None},
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or env_bool("TESTING", False)
            else "core.storage.StaticStorage"
        ),
    },
}
EXPORT_ROOT = Path(env("EXPORT_ROOT", "/data/exports"))

# Uploads ---------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
UPLOAD_MAX_BYTES = env_int("UPLOAD_MAX_BYTES", 10 * 1024 * 1024)
UPLOAD_MAX_PIXELS = env_int("UPLOAD_MAX_PIXELS", 40_000_000)
UPLOAD_MIN_SIDE = env_int("UPLOAD_MIN_SIDE", 200)
UPLOAD_STORED_MAX_SIDE = env_int("UPLOAD_STORED_MAX_SIDE", 3000)
UPLOADS_PER_CONTRIBUTION = env_int("UPLOADS_PER_CONTRIBUTION", 6)

# Open Food Facts ---------------------------------------------------------------
OFF_BASE_URL = env("OFF_BASE_URL", "https://world.openfoodfacts.org")
OFF_USER_AGENT = env("OFF_USER_AGENT", "olaHealth/0.1 (set OFF_USER_AGENT with a contact)")
OFF_CONNECT_TIMEOUT = float(env("OFF_CONNECT_TIMEOUT", "3"))
OFF_READ_TIMEOUT = float(env("OFF_READ_TIMEOUT", "10"))
OFF_MAX_RETRIES = env_int("OFF_MAX_RETRIES", 1)
# After a timeout / 5xx, skip OFF for this many seconds so users get an answer at once.
OFF_CIRCUIT_SECONDS = env_int("OFF_CIRCUIT_SECONDS", 60)
OFF_RETRY_AFTER_CAP = env_int("OFF_RETRY_AFTER_CAP", 5)
# OFF documents 15 product reads/min/IP. Shared by every web and worker process.
OFF_RATE_LIMIT_PER_MINUTE = env_int("OFF_RATE_LIMIT_PER_MINUTE", 12)
OFF_IMAGE_HOSTS = env_list("OFF_IMAGE_HOSTS", "images.openfoodfacts.org,static.openfoodfacts.org")
OFF_FRESH_TTL_HOURS = env_int("OFF_FRESH_TTL_HOURS", 24 * 7)
OFF_NEGATIVE_TTL_MINUTES = env_int("OFF_NEGATIVE_TTL_MINUTES", 30)
OFF_LEASE_SECONDS = env_int("OFF_LEASE_SECONDS", 30)
OFF_REFRESH_BATCH = env_int("OFF_REFRESH_BATCH", 20)

# Abuse limits (per session and per IP, fixed windows) -------------------------
RATE_LIMITS = {
    "lookup": (env_int("RL_LOOKUP_PER_MIN", 30), 60),
    "upload": (env_int("RL_UPLOAD_PER_HOUR", 40), 3600),
    "scan_photo": (env_int("RL_SCAN_PHOTO_PER_HOUR", 30), 3600),
    "contribution": (env_int("RL_CONTRIBUTION_PER_HOUR", 10), 3600),
    "ocr_retry": (env_int("RL_OCR_RETRY_PER_HOUR", 10), 3600),
}
TRUST_X_FORWARDED_FOR = env_bool("TRUST_X_FORWARDED_FOR", False)

# Retention ---------------------------------------------------------------------
DRAFT_RETENTION_DAYS = env_int("DRAFT_RETENTION_DAYS", 30)
REJECTED_IMAGE_RETENTION_DAYS = env_int("REJECTED_IMAGE_RETENTION_DAYS", 30)

# OCR ---------------------------------------------------------------------------
OCR_LANGUAGES = "eng+ron"
OCR_TIMEOUT_SECONDS = env_int("OCR_TIMEOUT_SECONDS", 40)
OCR_MAX_ATTEMPTS = env_int("OCR_MAX_ATTEMPTS", 2)
OCR_MAX_SIDE = env_int("OCR_MAX_SIDE", 2000)

# Django-Q2 with the PostgreSQL ORM broker ------------------------------------
Q_CLUSTER = {
    "name": "olahealth",
    "orm": "default",
    "workers": env_int("Q_WORKERS", 2),  # photo downloads wait on the network
    "recycle": 50,
    "timeout": OCR_TIMEOUT_SECONDS + 20,
    "retry": OCR_TIMEOUT_SECONDS + 60,
    "max_attempts": 2,
    "save_limit": 500,
    "ack_failures": True,
    "catch_up": False,
    "poll": 1,
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # "format" is our barcode-symbology parameter, not a renderer override.
    "URL_FORMAT_OVERRIDE": None,
}

# Security ----------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_HTTPONLY = False  # JS reads it to send fetch() requests
if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}},
}

SITE_NAME = "FoodoTrack"

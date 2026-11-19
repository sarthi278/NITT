from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "phase-0-development-secret")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
DJANGO_ENV = os.getenv("DJANGO_ENV", "development").strip().lower()
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1,0.0.0.0,testserver",
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core.apps.CoreConfig",

]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.AuditLogMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

if os.getenv("DATABASE_HOST"):
    database_options = {}
    ssl_mode = os.getenv("DATABASE_SSLMODE")
    if ssl_mode:
        database_options["sslmode"] = ssl_mode

    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DATABASE_NAME", "order_audit"),
        "USER": os.getenv("DATABASE_USER", "order_audit"),
        "PASSWORD": os.getenv("DATABASE_PASSWORD", "order_audit"),
        "HOST": os.getenv("DATABASE_HOST"),
        "PORT": os.getenv("DATABASE_PORT", "5432"),
        "OPTIONS": database_options,
    }

AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

# Production security settings
from django.core.exceptions import ImproperlyConfigured

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

if DJANGO_ENV == "production" and DEBUG:
    raise ImproperlyConfigured("Set DJANGO_DEBUG=0 in production.")

if not DEBUG:
    development_secrets = {
        "",
        "phase-0-development-secret",
        "local-only-change-me",
        "change-me",
    }
    if SECRET_KEY in development_secrets:
        raise ImproperlyConfigured(
            "Set a private, non-development DJANGO_SECRET_KEY in production."
        )
    if not os.getenv("DATABASE_HOST"):
        raise ImproperlyConfigured("Configure DATABASE_HOST for production PostgreSQL.")
    if os.getenv("DATABASE_PASSWORD", "").strip().lower() in {
        "",
        "order_audit",
        "local-dev-password",
        "password",
        "change-me",
    }:
        raise ImproperlyConfigured(
            "Set a private, non-development DATABASE_PASSWORD in production."
        )
    if not os.getenv("DJANGO_ALLOWED_HOSTS", "").strip():
        raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS in production.")
    if not CSRF_TRUSTED_ORIGINS:
        raise ImproperlyConfigured(
            "Set HTTPS origins in DJANGO_CSRF_TRUSTED_ORIGINS for production."
        )

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Reset operations are allowed only on a separately named development database.
BENCHMARK_DEMO_DATABASE = os.getenv("BENCHMARK_DEMO_DATABASE", "")
BENCHMARK_RESET_ENABLED = os.getenv("BENCHMARK_RESET_ENABLED", "0") == "1"

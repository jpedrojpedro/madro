import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # Single App - MADRO
    "madro",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "madro.urls"
WSGI_APPLICATION = "madro.wsgi.application"
ASGI_APPLICATION = "madro.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "OPTIONS": {"conninfo": os.environ.get("DATABASE_URL", "postgres://madro:madro@localhost:5432/madro")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

RETRIEVAL_DB_URL = os.environ.get("RETRIEVAL_DB_URL")

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

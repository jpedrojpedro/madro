import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

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
        "NAME": None,
        "OPTIONS": {
            "conninfo": os.environ.get("DATABASE_URL", "postgresql://madro:madro@localhost:5432/madro").replace("postgres://", "postgresql://", 1)
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

RETRIEVAL_DB_URL = os.environ.get("RETRIEVAL_DB_URL")

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

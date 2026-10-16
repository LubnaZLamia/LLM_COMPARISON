"""
Django settings for the LLM Health Assistant project.

All secrets / tunables are read from a `.env` file in the project root.
Copy `.env.example` to `.env` and fill in your OpenRouter key.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps
    "apps.core",
    "apps.dataset",
    "apps.llms",
    "apps.comparisons",
    "apps.ratings",
    "apps.chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "apps.core.context_processors.app_status",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "America/New_York")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- LLM / OpenRouter -------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_TIMEOUT = int(os.getenv("OPENROUTER_TIMEOUT", "90"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
# 600 was too low: models that "think" before answering (Gemini, reasoning models)
# spend part of the budget internally and get cut off mid-sentence.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1500"))
# Temporary failures (HTTP 429 "provider busy", 5xx, timeouts, empty replies) are retried.
OPENROUTER_MAX_ATTEMPTS = int(os.getenv("OPENROUTER_MAX_ATTEMPTS", "3"))
OPENROUTER_RETRY_BACKOFF = float(os.getenv("OPENROUTER_RETRY_BACKOFF", "1.0"))
# OpenRouter id of the model that scores outputs when "Score with judge model" is ticked.
# Leave blank to use the model marked "is judge" in admin.
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "").strip()
# Ground-truth matching: how much of a clinician question's wording must appear
# in a model question before it counts as asked (0-1). Lower = more generous.
# Used by the lexical (baseline) matcher only.
GT_MATCH_RECALL = float(os.getenv("GT_MATCH_RECALL", "0.5"))
# Chat memory: how many recent messages are sent back to the model with each new one.
# Older messages stay saved, but the model no longer sees them. Keeps long chats fast and cheap.
CHAT_HISTORY_MESSAGES = int(os.getenv("CHAT_HISTORY_MESSAGES", "20"))

# --- Clinician matching -----------------------------------------------------
# How the clinician comparison is scored:
#   lexical  word overlap only (the original baseline)
#   meaning  a small model aligns the two texts, so a paraphrase still counts
#   both     compute both and show them side by side (the default)
MATCH_METHOD = os.getenv("MATCH_METHOD", "both").strip().lower()
# The model that does the alignment. Blank picks a cheap active model.
# It goes through the same endpoint and key as every other call.
MATCH_MODEL = os.getenv("MATCH_MODEL", "").strip()
# When true, no API calls are made; each model returns a canned answer.
# Handy for working on the UI without spending credits.
LLM_MOCK_MODE = env_bool("LLM_MOCK_MODE", False)

"""
Django base settings - 공통 설정
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env file (부모 폴더도 로드)
load_dotenv(BASE_DIR.parent / '.env')  # 부모 폴더 먼저
load_dotenv(BASE_DIR / '.env')  # 프로젝트 폴더 (덮어쓰기 가능)

# Parent directory (기존 long_form_video)
LONG_FORM_VIDEO_DIR = BASE_DIR.parent
SCRIPTS_DIR = LONG_FORM_VIDEO_DIR / 'scripts'
OUTPUT_DIR = LONG_FORM_VIDEO_DIR / 'output'
AGENTS_DIR = LONG_FORM_VIDEO_DIR / '.claude' / 'agents'

# Gemini 설정
GEMINI_MODEL = 'gemini-3-pro-preview'
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

# Fish Speech TTS 설정
FISH_SPEECH_URL = 'http://118.216.98.160:9881'

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-dev-key-change-in-production')


# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "apps.accounts",
    "apps.pipeline",
    "apps.prompts",
    "apps.api",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# 동시 자동 파이프라인 실행 제한
MAX_CONCURRENT_PIPELINES = 2


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Custom User Model
AUTH_USER_MODEL = 'accounts.User'


# Internationalization
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"


# Media files (uploads)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Login settings
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'pipeline:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

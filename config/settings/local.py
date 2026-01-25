"""
Django local settings - 로컬 개발용
"""
from .base import *

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1']


# Database - SQLite (로컬 개발용)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 30,  # 락 대기 시간 (초)
        },
    }
}


# 로컬에서는 이메일 콘솔 출력
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


# Debug toolbar (선택사항)
# INSTALLED_APPS += ['debug_toolbar']
# MIDDLEWARE = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE
# INTERNAL_IPS = ['127.0.0.1']

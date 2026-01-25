"""
Django settings - 하위 호환용 리다이렉트

새 설정 구조:
- config.settings.base: 공통 설정
- config.settings.local: 로컬 개발 (SQLite)
- config.settings.production: 프로덕션 (PostgreSQL)

사용법:
- 로컬: DJANGO_SETTINGS_MODULE=config.settings.local
- 프로덕션: DJANGO_SETTINGS_MODULE=config.settings.production
"""

# 기존 코드와 호환성을 위해 local 설정 import
from config.settings.local import *

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect

urlpatterns = [
    path('', lambda r: redirect('pipeline:dashboard')),
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('pipeline/', include('apps.pipeline.urls')),
    path('api/', include('apps.api.urls')),
]

# 개발 환경에서 미디어 파일 서빙
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

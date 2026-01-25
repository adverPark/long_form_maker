from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.views.static import serve

urlpatterns = [
    path('', lambda r: redirect('pipeline:dashboard')),
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('pipeline/', include('apps.pipeline.urls')),
    path('api/', include('apps.api.urls')),
]

# 미디어 파일 서빙 (Nginx 없이 Gunicorn 단독 사용 시)
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

# 개발 환경에서 정적 파일 서빙
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

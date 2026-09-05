"""Корневая схема маршрутов ИС «ГрузПоток»."""

from core.views import health
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Публичная часть.
    path("", include("core.urls", namespace="core")),
    path("analytics/", include("analytics.urls", namespace="analytics")),
    path("materials/", include("content.urls", namespace="content")),
    # Личный кабинет и аутентификация.
    path("account/", include("accounts.urls", namespace="accounts")),
    path("", include("accounts.auth_urls")),
    path("export/", include("exports.urls", namespace="exports")),
    # Панель администратора системы и штатная админка Django.
    path("console/", include("console.urls", namespace="console")),
    path("django-admin/", admin.site.urls),
    # Программный интерфейс.
    path("api/v1/", include(("api.urls", "api"), namespace="v1")),
    # Служебное.
    path("i18n/", include("django.conf.urls.i18n")),
    path("healthz", health, name="health"),
]

if settings.DEBUG:
    # В отладочном режиме файлы пользователей отдаёт сам сервер разработки.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

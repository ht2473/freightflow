"""Обработчики сигналов модуля учётных записей."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from .models import AuditEvent, Role, UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="create_user_profile")
def create_user_profile(sender, instance, created: bool, **kwargs) -> None:
    """Создать профиль сразу после регистрации учётной записи.

    Суперпользователь, создаваемый командой createsuperuser, получает роль
    администратора — иначе после установки системы в неё некому было бы войти
    с полными правами.
    """
    if not created:
        return
    UserProfile.objects.get_or_create(
        user=instance,
        defaults={"role": Role.ADMIN if instance.is_superuser else Role.VIEWER},
    )


@receiver(user_logged_in, dispatch_uid="audit_login")
def audit_login(sender, request, user, **kwargs) -> None:
    """Зафиксировать вход пользователя в журнале действий."""
    AuditEvent.objects.create(
        user=user,
        action=AuditEvent.Action.LOGIN,
        summary=str(_("Выполнен вход в систему")),
        path=request.path if request else "",
        ip_address=_client_ip(request),
        request_id=getattr(request, "request_id", ""),
    )


@receiver(user_logged_out, dispatch_uid="audit_logout")
def audit_logout(sender, request, user, **kwargs) -> None:
    """Зафиксировать выход пользователя из системы."""
    if not user:
        return
    AuditEvent.objects.create(
        user=user,
        action=AuditEvent.Action.LOGOUT,
        summary=str(_("Выполнен выход из системы")),
        path=request.path if request else "",
        ip_address=_client_ip(request),
        request_id=getattr(request, "request_id", ""),
    )


def _client_ip(request) -> str | None:
    """Определить адрес клиента с учётом работы за обратным прокси."""
    if not request:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Первый адрес в цепочке — исходный клиент.
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

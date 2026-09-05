"""Создание групп разрешений, соответствующих ролям системы.

Роль пользователя хранится в его профиле и определяет доступ на уровне
представлений. Группы Django создаются дополнительно: они дают привычный
набор прав в штатной админке и позволяют выдавать точечные разрешения без
изменения кода.

Модель прав вложенная: каждая последующая роль включает разрешения всех
предыдущих. Команда идемпотентна — её можно выполнять при каждом обновлении.
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import ROLE_DESCRIPTIONS, Role

# Разрешения, добавляемые ролью сверх предыдущей. Формат: «приложение.действие_модель».
ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.VIEWER: [
        "core.view_district",
        "core.view_infrastructureobject",
        "core.view_infrastructuretype",
        "core.view_cargocategory",
        "core.view_cargoroute",
        "core.view_roadsegment",
        "core.view_trafficcondition",
        "core.view_trafficincident",
        "core.view_freightflowstat",
        "core.view_datasource",
        "content.view_article",
    ],
    Role.ANALYST: [
        "core.view_etlrun",
        "accounts.add_exportjob",
        "accounts.view_exportjob",
        "accounts.add_comparisonset",
        "accounts.change_comparisonset",
        "accounts.delete_comparisonset",
    ],
    Role.OPERATOR: [
        "core.add_trafficincident",
        "core.change_trafficincident",
        "core.change_infrastructureobject",
        "core.add_infrastructureobject",
        "core.add_etlrun",
        "core.change_etlrun",
    ],
    Role.ADMIN: [
        "auth.view_user",
        "auth.change_user",
        "content.add_article",
        "content.change_article",
        "content.delete_article",
        "content.view_feedbackmessage",
        "content.change_feedbackmessage",
        "accounts.view_auditevent",
        "accounts.view_userprofile",
        "accounts.change_userprofile",
        "core.add_district",
        "core.change_district",
        "core.change_datasource",
        "core.add_datasource",
    ],
}


class Command(BaseCommand):
    """Создать и наполнить группы разрешений."""

    help = "Создание групп Django, соответствующих ролям системы"

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        accumulated: list[Permission] = []

        for role, _label in Role.choices:
            group, created = Group.objects.get_or_create(name=f"role:{role}")
            found, absent = self._resolve(ROLE_PERMISSIONS.get(role, []))
            accumulated.extend(found)

            # Вложенность: группа получает и собственные разрешения, и все
            # разрешения ролей более низкого уровня.
            group.permissions.set(accumulated)

            state = "создана" if created else "обновлена"
            self.stdout.write(
                f"  {group.name:<18} {state}, разрешений: {len(accumulated):>3}"
                + (f", не найдено: {len(absent)}" if absent else "")
            )
            for code in absent:
                self.stderr.write(f"      отсутствует разрешение {code}")

        self.stdout.write("")
        for role, label in Role.choices:
            self.stdout.write(f"  {label}: {ROLE_DESCRIPTIONS.get(role, '')}")
        self.stdout.write(self.style.SUCCESS("\nГруппы разрешений настроены"))

    @staticmethod
    def _resolve(codes: list[str]) -> tuple[list[Permission], list[str]]:
        """Найти объекты разрешений по строковым кодам."""
        found: list[Permission] = []
        absent: list[str] = []
        for code in codes:
            app_label, codename = code.split(".", 1)
            permission = Permission.objects.filter(
                content_type__app_label=app_label, codename=codename
            ).first()
            if permission:
                found.append(permission)
            else:
                absent.append(code)
        return found, absent

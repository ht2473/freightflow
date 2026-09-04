"""Порождение уведомлений событиями системы.

Уведомление появляется только как след произошедшего: загрузка данных
принесла событие, подпадающее под условия подписки; проверки качества
отложили записи в карантин; администратор изменил роль. Ничего, что
не случилось, в этом разделе кабинета не показывается.

Оповещение о дорожных событиях сводное. Загрузка приносит сотни записей
разом, и отдельное уведомление о каждой сделало бы раздел непригодным
к чтению уже на первом же обновлении данных: подписчик получает одну
запись с числом событий и ссылкой на их перечень.
"""

from __future__ import annotations

from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from .models import IncidentSubscription, Notification, Role, UserProfile

#: Наибольшее число уведомлений, создаваемых одним событием системы.
#: Ограничение защищает от рассылки по всему списку пользователей, если
#: подписки когда-нибудь начнут заводиться массово.
MAX_RECIPIENTS = 500


def incidents_loaded(incidents) -> int:
    """Оповестить подписчиков о новых дорожных событиях.

    Возвращает число созданных уведомлений. Подписки перебираются по
    событиям, а не наоборот: условия отбора живут в самой подписке
    (:meth:`IncidentSubscription.matches`), и повторять их запросом
    к базе означало бы описать те же правила дважды.
    """
    incidents = [item for item in incidents if item is not None]
    if not incidents:
        return 0

    created = 0
    subscriptions = (
        IncidentSubscription.objects.filter(is_active=True)
        .select_related("district", "user")
    )
    for subscription in subscriptions[:MAX_RECIPIENTS]:
        matched = [item for item in incidents if subscription.matches(item)]
        if not matched:
            continue
        Notification.objects.create(
            user=subscription.user,
            level=_level_for(matched),
            title=ngettext(
                "Новое событие на дорогах: %(count)d",
                "Новые события на дорогах: %(count)d",
                len(matched),
            ) % {"count": len(matched)},
            body=_("%(scope)s. Наибольшая серьёзность — %(severity)d из 5.") % {
                "scope": subscription.scope_label,
                "severity": max(item.severity for item in matched),
            },
            url=matched[0].get_absolute_url() if len(matched) == 1 else subscription.url,
        )
        created += 1
    return created


def load_failed(report, *, source_title: str) -> int:
    """Оповестить о загрузке, завершившейся отказом."""
    return _to_operators(
        level=Notification.Level.ALERT,
        title=_("Загрузка «%(source)s» не выполнена") % {"source": source_title},
        body="; ".join(report.notes)[:1000] or _("Причина указана в журнале загрузок."),
        url=_run_url(report),
    )


def quarantined(report, *, source_title: str) -> int:
    """Оповестить о записях, отложенных проверками качества в карантин."""
    return _to_operators(
        level=Notification.Level.WARNING,
        title=ngettext(
            "Карантин: %(count)d запись из загрузки «%(source)s»",
            "Карантин: %(count)d записей из загрузки «%(source)s»",
            report.rejected,
        ) % {"count": report.rejected, "source": source_title},
        body=_("Причины: %(checks)s") % {
            "checks": ", ".join(sorted(report.by_check)) or _("не указаны")
        },
        url=reverse("console:quarantine"),
    )


def role_changed(user, *, role: str, actor) -> None:
    """Сообщить пользователю о смене его роли.

    Изменение полномочий человек обязан заметить: от роли зависит и состав
    разделов, и доступность программного интерфейса.
    """
    Notification.objects.create(
        user=user,
        level=Notification.Level.INFO,
        title=_("Роль изменена: %(role)s") % {"role": Role(role).label},
        body=_("Полномочия учётной записи изменены администратором %(actor)s.") % {
            "actor": actor.get_full_name() or actor.username
        },
        url=reverse("accounts:profile"),
    )


def _to_operators(*, level: str, title: str, body: str, url: str) -> int:
    """Разослать уведомление тем, кто отвечает за состояние данных."""
    profiles = UserProfile.objects.filter(
        Q(role=Role.OPERATOR) | Q(role=Role.ADMIN), user__is_active=True
    ).select_related("user")[:MAX_RECIPIENTS]
    notifications = [
        Notification(user=profile.user, level=level, title=title, body=body, url=url)
        for profile in profiles
    ]
    Notification.objects.bulk_create(notifications)
    return len(notifications)


def _level_for(incidents) -> str:
    """Уровень сводного уведомления определяется самым серьёзным событием."""
    severity = max(item.severity for item in incidents)
    if severity >= 4:
        return Notification.Level.ALERT
    if severity >= 3:
        return Notification.Level.WARNING
    return Notification.Level.INFO


def _run_url(report) -> str:
    """Адрес карточки загрузки, если она попала в журнал."""
    if report.run_id:
        return reverse("console:etl_run", args=[report.run_id])
    return reverse("console:etl")

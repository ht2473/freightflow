"""Панель администратора информационной системы.

Собственная панель управления решает задачи, для которых штатная админка
Django неудобна: сводная картина состояния системы, работа с обращениями в
привычных для оператора терминах, контроль качества данных и наблюдение за
процедурами загрузки. Штатная админка при этом сохранена и доступна по
адресу ``/django-admin/`` для низкоуровневых операций со справочниками.

Доступ ко всем разделам ограничен ролью «Администратор»; проверка выполняется
декоратором :func:`admin_required`, а не только скрытием пунктов меню.
"""

from __future__ import annotations

from datetime import timedelta
from functools import wraps

from accounts.models import AuditEvent, ExportJob, Role, UserProfile, profile_for
from content.models import Article, ArticleCategory, FeedbackMessage
from core import selectors
from core.models import (
    CargoCategory,
    DataSource,
    District,
    EtlRun,
    InfrastructureObject,
    InfrastructureType,
    RoadSegment,
    TrafficIncident,
)
from core.views.base import choice_param, int_param, page_context, paginate
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

# Разделы панели: код, подпись, маршрут, краткое пояснение.
CONSOLE_TABS: tuple[tuple[str, str, str, str], ...] = (
    ("dashboard", _("Обзор системы"), "console:dashboard", _("Состояние и ключевые счётчики")),
    ("users", _("Пользователи"), "console:users", _("Учётные записи и роли")),
    ("references", _("Справочники"), "console:references", _("Округа, типы, категории, источники")),
    ("feedback", _("Обращения"), "console:feedback", _("Обратная связь и ответы")),
    ("content", _("Материалы"), "console:content", _("Аналитические публикации")),
    ("quality", _("Качество данных"), "console:quality", _("Полнота и целостность записей")),
    ("etl", _("Загрузка данных"), "console:etl", _("Журнал и запуск процедур")),
    ("audit", _("Журнал аудита"), "console:audit", _("Действия всех пользователей")),
    ("system", _("Состояние среды"), "console:system", _("Параметры развёртывания")),
)


def admin_required(view):
    """Пропустить в панель только пользователей с ролью «Администратор»."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        profile = profile_for(request.user)
        if not (request.user.is_superuser or (profile and profile.can_administer)):
            raise PermissionDenied(_("Раздел доступен администраторам системы"))
        return view(request, *args, **kwargs)

    return wrapper


def _console_context(request, *, title: str, tab: str, lead: str = "", **extra) -> dict:
    """Общий контекст страниц панели администратора."""
    return page_context(
        request,
        title=title,
        lead=lead,
        active="console",
        crumbs=[(_("Панель администратора"), "console:dashboard"), (title,)],
        tabs=CONSOLE_TABS,
        current_tab=tab,
        pending_feedback=FeedbackMessage.objects.filter(
            status__in=[FeedbackMessage.Status.NEW, FeedbackMessage.Status.IN_WORK]
        ).count(),
        **extra,
    )


# ---------------------------------------------------------------------------
#  1. Обзор системы
# ---------------------------------------------------------------------------


@admin_required
def dashboard(request):
    """Сводная страница состояния системы."""
    week_ago = timezone.now() - timedelta(days=7)

    users = User.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        recent=Count("id", filter=Q(last_login__gte=week_ago)),
    )
    by_role = list(
        UserProfile.objects.values("role").annotate(count=Count("id")).order_by("role")
    )
    role_labels = dict(Role.choices)
    for row in by_role:
        row["label"] = role_labels.get(row["role"], row["role"])

    context = _console_context(
        request,
        title=_("Обзор системы"),
        tab="dashboard",
        lead=_("Ключевые счётчики, состояние данных и активность пользователей."),
        summary=selectors.dashboard_summary(),
        users=users,
        by_role=by_role,
        feedback_new=FeedbackMessage.objects.filter(status=FeedbackMessage.Status.NEW).count(),
        feedback_total=FeedbackMessage.objects.count(),
        articles_published=Article.objects.filter(is_published=True).count(),
        exports_week=ExportJob.objects.filter(created_at__gte=week_ago).count(),
        etl=selectors.etl_health(limit=5),
        recent_events=AuditEvent.objects.select_related("user")[:10],
        incidents_open=TrafficIncident.objects.open().count(),
        coverage=selectors.data_coverage(),
    )
    return render(request, "console/dashboard.html", context)


# ---------------------------------------------------------------------------
#  2. Пользователи и роли
# ---------------------------------------------------------------------------


@admin_required
def users(request):
    """Управление учётными записями и ролями."""
    queryset = User.objects.select_related("profile").order_by("-date_joined")

    role = choice_param(request, "role", [value for value, _ in Role.choices])
    term = (request.GET.get("q") or "").strip()
    state = choice_param(request, "state", ["active", "blocked"])

    if role:
        queryset = queryset.filter(profile__role=role)
    if state == "active":
        queryset = queryset.filter(is_active=True)
    elif state == "blocked":
        queryset = queryset.filter(is_active=False)
    if term:
        queryset = queryset.filter(
            Q(username__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
        )

    context = _console_context(
        request,
        title=_("Пользователи"),
        tab="users",
        lead=_(
            "Учётные записи, назначенные роли и состояние доступа. Роль "
            "определяет объём полномочий во всех разделах системы."
        ),
        page_obj=paginate(request, queryset, per_page=25),
        total_count=queryset.count(),
        roles=Role.choices,
        filters={"role": role, "q": term, "state": state},
    )
    return render(request, "console/users.html", context)


@require_POST
@admin_required
def user_action(request, pk: int):
    """Изменить роль пользователя либо заблокировать учётную запись."""
    user = get_object_or_404(User, pk=pk)
    profile = profile_for(user)
    action = request.POST.get("action", "")

    if user == request.user and action in {"block", "set_role"}:
        # Защита от самоблокировки: администратор не должен случайно лишить
        # себя доступа к панели, оставив систему без управления.
        messages.error(request, _("Нельзя изменить роль или заблокировать собственную запись."))
        return redirect("console:users")

    if action == "set_role":
        new_role = request.POST.get("role", "")
        if new_role in dict(Role.choices):
            profile.role = new_role
            profile.save(update_fields=["role", "updated_at"])
            AuditEvent.objects.create(
                user=request.user,
                action=AuditEvent.Action.ADMIN,
                entity="user",
                entity_id=str(user.pk),
                summary=f"Роль пользователя {user.username} изменена на «{profile.get_role_display()}»",
                request_id=getattr(request, "request_id", ""),
            )
            messages.success(request, f"Роль пользователя обновлена: {profile.get_role_display()}.")
    elif action == "block":
        user.is_active = False
        user.save(update_fields=["is_active"])
        messages.info(request, f"Учётная запись {user.username} заблокирована.")
    elif action == "unblock":
        user.is_active = True
        user.save(update_fields=["is_active"])
        messages.success(request, f"Доступ для {user.username} восстановлен.")

    request.audit_written = True
    return redirect("console:users")


# ---------------------------------------------------------------------------
#  3. Справочники
# ---------------------------------------------------------------------------


@admin_required
def references(request):
    """Сводка справочников системы."""
    context = _console_context(
        request,
        title=_("Справочники"),
        tab="references",
        lead=_(
            "Классификаторы, на которых строится учёт. Изменение справочника "
            "затрагивает все связанные записи, поэтому правки выполняются "
            "через штатную админку с контролем ссылочной целостности."
        ),
        districts=District.objects.annotate(objects_count=Count("facilities")),
        types=InfrastructureType.objects.annotate(objects_count=Count("facilities")),
        categories=CargoCategory.objects.annotate(flows_count=Count("flow_stats")),
        sources=DataSource.objects.annotate(runs_count=Count("etl_runs")),
    )
    return render(request, "console/references.html", context)


# ---------------------------------------------------------------------------
#  4. Обращения
# ---------------------------------------------------------------------------


@admin_required
def feedback(request):
    """Работа с обращениями пользователей."""
    queryset = FeedbackMessage.objects.select_related("answered_by")

    status = choice_param(request, "status", [v for v, _ in FeedbackMessage.Status.choices])
    topic = choice_param(request, "topic", [v for v, _ in FeedbackMessage.Topic.choices])
    if status:
        queryset = queryset.filter(status=status)
    if topic:
        queryset = queryset.filter(topic=topic)

    context = _console_context(
        request,
        title=_("Обращения"),
        tab="feedback",
        lead=_("Сообщения из формы обратной связи и подготовка ответов."),
        page_obj=paginate(request, queryset.order_by("-created_at"), per_page=15),
        total_count=queryset.count(),
        statuses=FeedbackMessage.Status.choices,
        topics=FeedbackMessage.Topic.choices,
        by_status=list(
            FeedbackMessage.objects.values("status").annotate(count=Count("id"))
        ),
        filters={"status": status, "topic": topic},
    )
    return render(request, "console/feedback.html", context)


@admin_required
def feedback_detail(request, pk: int):
    """Карточка обращения с формой ответа."""
    message = get_object_or_404(FeedbackMessage, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "answer":
            answer = (request.POST.get("answer") or "").strip()
            if len(answer) < 10:
                messages.error(request, _("Текст ответа слишком короткий."))
            else:
                message.mark_answered(request.user, answer)
                messages.success(request, _("Ответ сохранён, обращение переведено в состояние «Отвечено»."))
                return redirect("console:feedback_detail", pk=pk)
        elif action == "in_work":
            message.status = FeedbackMessage.Status.IN_WORK
            message.save(update_fields=["status"])
            messages.info(request, _("Обращение взято в работу."))
            return redirect("console:feedback_detail", pk=pk)
        elif action == "close":
            message.status = FeedbackMessage.Status.CLOSED
            message.save(update_fields=["status"])
            messages.info(request, _("Обращение закрыто."))
            return redirect("console:feedback")

    context = _console_context(
        request,
        title=f"Обращение № {message.pk}",
        tab="feedback",
        lead=f"{message.get_topic_display()} · {message.name}",
        message=message,
    )
    return render(request, "console/feedback_detail.html", context)


# ---------------------------------------------------------------------------
#  5. Материалы
# ---------------------------------------------------------------------------


@admin_required
def content(request):
    """Управление аналитическими публикациями."""
    queryset = Article.objects.select_related("category", "author").order_by("-published_at")

    state = choice_param(request, "state", ["published", "draft"])
    if state == "published":
        queryset = queryset.filter(is_published=True)
    elif state == "draft":
        queryset = queryset.filter(is_published=False)

    context = _console_context(
        request,
        title=_("Материалы"),
        tab="content",
        lead=_("Аналитические обзоры портала: публикация, снятие, вынос на главную."),
        page_obj=paginate(request, queryset, per_page=20),
        categories=ArticleCategory.objects.annotate(total=Count("articles")),
        published=Article.objects.filter(is_published=True).count(),
        drafts=Article.objects.filter(is_published=False).count(),
        total_views=Article.objects.aggregate(total=Sum("view_count"))["total"] or 0,
        filters={"state": state},
    )
    return render(request, "console/content.html", context)


@require_POST
@admin_required
def content_action(request, pk: int):
    """Переключить состояние публикации материала."""
    article = get_object_or_404(Article, pk=pk)
    action = request.POST.get("action", "")

    if action == "publish":
        article.is_published = True
    elif action == "unpublish":
        article.is_published = False
    elif action == "feature":
        article.is_featured = not article.is_featured
    article.save(update_fields=["is_published", "is_featured"])
    messages.success(request, f"Материал «{article.title}» обновлён.")
    request.audit_written = True
    return redirect("console:content")


# ---------------------------------------------------------------------------
#  6. Качество данных
# ---------------------------------------------------------------------------


@admin_required
def quality(request):
    """Контроль полноты и непротиворечивости данных.

    Проверки отобраны по принципу «дефект, искажающий выводы»: отсутствие
    координат исключает объект с карты, отрицательные или нулевые мощности
    искажают агрегаты, инциденты без привязки к участку не попадают в
    статистику по округам.
    """
    checks = [
        {
            "title": _("Объекты без координат"),
            "detail": _("Не отображаются на карте и не участвуют в поиске «что рядом»"),
            "count": InfrastructureObject.objects.filter(geom__isnull=True).count(),
            "total": InfrastructureObject.objects.count(),
            "severity": "warn",
        },
        {
            "title": _("Объекты без указания мощности"),
            "detail": _("Не учитываются в расчёте обеспеченности округа"),
            "count": InfrastructureObject.objects.filter(capacity_tons__isnull=True).count(),
            "total": InfrastructureObject.objects.count(),
            "severity": "warn",
        },
        {
            "title": _("Объекты без адреса"),
            "detail": _("Затрудняет идентификацию объекта пользователем"),
            "count": InfrastructureObject.objects.filter(
                Q(address__isnull=True) | Q(address="")
            ).count(),
            "total": InfrastructureObject.objects.count(),
            "severity": "info",
        },
        {
            "title": _("Участки сети без геометрии"),
            "detail": _("Не отображаются на слое дорожной сети"),
            "count": RoadSegment.objects.filter(geom__isnull=True).count(),
            "total": RoadSegment.objects.count(),
            "severity": "warn",
        },
        {
            "title": _("Инциденты без привязки к участку"),
            "detail": _("Не попадают в статистику аварийности по округам"),
            "count": TrafficIncident.objects.filter(road__isnull=True).count(),
            "total": TrafficIncident.objects.count(),
            "severity": "warn",
        },
        {
            "title": _("Незакрытые инциденты старше 30 суток"),
            "detail": _("Вероятно, отсутствует отметка об устранении"),
            "count": TrafficIncident.objects.filter(
                resolved_at__isnull=True, reported_at__lt=timezone.now() - timedelta(days=30)
            ).count(),
            "total": TrafficIncident.objects.filter(resolved_at__isnull=True).count(),
            "severity": "alert",
        },
        {
            "title": _("Записи без указания источника"),
            "detail": _("Невозможно проследить происхождение сведений"),
            "count": InfrastructureObject.objects.filter(source__isnull=True).count(),
            "total": InfrastructureObject.objects.count(),
            "severity": "info",
        },
    ]
    for check in checks:
        check["share"] = round(check["count"] / check["total"] * 100, 1) if check["total"] else 0.0

    context = _console_context(
        request,
        title=_("Качество данных"),
        tab="quality",
        lead=_(
            "Автоматические проверки полноты и согласованности сведений. "
            "Выявленные дефекты не блокируют работу системы, но снижают "
            "достоверность аналитических выводов."
        ),
        checks=checks,
        coverage=selectors.data_coverage(),
        issues_total=sum(check["count"] for check in checks),
    )
    return render(request, "console/quality.html", context)


# ---------------------------------------------------------------------------
#  7. Загрузка данных
# ---------------------------------------------------------------------------


@admin_required
def etl(request):
    """Журнал процедур загрузки и запуск обновления."""
    queryset = EtlRun.objects.select_related("source").order_by("-started_at")

    status = choice_param(request, "status", ["running", "success", "partial", "failed"])
    source_id = int_param(request, "source")
    if status:
        queryset = queryset.filter(status=status)
    if source_id:
        queryset = queryset.filter(source_id=source_id)

    context = _console_context(
        request,
        title=_("Загрузка данных"),
        tab="etl",
        lead=_(
            "Хронология обновления сведений из внешних источников. "
            "Запуск процедуры вручную доступен для активных источников."
        ),
        page_obj=paginate(request, queryset, per_page=25),
        health=selectors.etl_health(limit=1),
        sources=DataSource.objects.all(),
        filters={"status": status, "source": source_id},
    )
    return render(request, "console/etl.html", context)


@require_POST
@admin_required
def cache_flush(request):
    """Сбросить кеш сводок и аналитических расчётов."""
    from analytics import services as analytics_services

    selectors.invalidate_caches()
    analytics_services.invalidate()
    messages.success(request, _("Кеш сводок и аналитических расчётов сброшен."))
    request.audit_written = True
    return redirect(request.POST.get("next") or "console:dashboard")


# ---------------------------------------------------------------------------
#  8. Журнал аудита
# ---------------------------------------------------------------------------


@admin_required
def audit(request):
    """Журнал действий всех пользователей системы."""
    queryset = AuditEvent.objects.select_related("user")

    action = choice_param(request, "action", [v for v, _ in AuditEvent.Action.choices])
    user_id = int_param(request, "user")
    days = int_param(request, "days", 30) or 30

    if action:
        queryset = queryset.filter(action=action)
    if user_id:
        queryset = queryset.filter(user_id=user_id)
    queryset = queryset.filter(created_at__gte=timezone.now() - timedelta(days=days))

    context = _console_context(
        request,
        title=_("Журнал аудита"),
        tab="audit",
        lead=_(
            "Регистрация значимых событий: входы, изменения данных, выгрузки "
            "и административные операции."
        ),
        page_obj=paginate(request, queryset.order_by("-created_at"), per_page=40),
        total_count=queryset.count(),
        actions=AuditEvent.Action.choices,
        users=User.objects.order_by("username"),
        by_action=list(queryset.values("action").annotate(count=Count("id")).order_by("-count")),
        filters={"action": action, "user": user_id, "days": days},
    )
    return render(request, "console/audit.html", context)


# ---------------------------------------------------------------------------
#  9. Состояние среды
# ---------------------------------------------------------------------------


@admin_required
def system(request):
    """Параметры развёртывания и состояние среды выполнения."""
    import platform
    import sys

    import django

    db_size = None
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            db_size = cursor.fetchone()[0]
    elif connection.vendor == "sqlite":
        path = settings.DATABASES["default"]["NAME"]
        try:
            db_size = f"{path.stat().st_size / 1024 / 1024:.1f} МБ"
        except (AttributeError, OSError):
            db_size = _("недоступно")

    parameters = [
        (_("Версия системы"), settings.PROJECT_VERSION),
        (_("Версия Django"), django.get_version()),
        (_("Версия Python"), sys.version.split()[0]),
        (_("Операционная система"), f"{platform.system()} {platform.release()}"),
        (_("Система управления БД"), connection.vendor),
        (_("Объём базы данных"), db_size or "—"),
        (_("Режим отладки"), _("включён") if settings.DEBUG else _("выключен")),
        (_("Часовой пояс"), settings.TIME_ZONE),
        (_("Язык по умолчанию"), settings.LANGUAGE_CODE),
        ("Кеш", settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]),
        (_("Каталог выгрузок"), str(settings.EXPORT_ROOT)),
        (_("Срок хранения выгрузок"), f"{settings.EXPORT_RETENTION_DAYS} сут."),
    ]

    context = _console_context(
        request,
        title=_("Состояние среды"),
        tab="system",
        lead=_("Параметры контура развёртывания и версии компонентов."),
        parameters=parameters,
        exports_total=ExportJob.objects.count(),
        exports_size=ExportJob.objects.aggregate(total=Sum("file_size"))["total"] or 0,
        audit_total=AuditEvent.objects.count(),
    )
    return render(request, "console/system.html", context)

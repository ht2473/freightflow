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

from accounts import notify
from accounts.models import AuditEvent, ExportJob, Role, UserProfile, profile_for
from content.models import Article, ArticleCategory, FeedbackMessage
from core import selectors
from core.choices import EtlTrigger
from core.models import (
    CargoCategory,
    DataSource,
    District,
    EtlReject,
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
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from etl import dispatch, registry, schedule
from etl.pipeline import PipelineError
from etl.upload import COLUMNS, template_csv

from .forms import FlowUploadForm, PipelineRunForm

# Разделы панели: код, подпись, маршрут, краткое пояснение, наименьшая роль.
# Роль здесь — единственное место, где объявлено, кому раздел принадлежит:
# по ней же строится перечень вкладок, поэтому показанное и доступное
# разойтись не могут.
CONSOLE_TABS: tuple[tuple[str, str, str, str, str], ...] = (
    ("dashboard", _("Обзор системы"), "console:dashboard",
     _("Состояние и ключевые счётчики"), Role.OPERATOR),
    ("etl", _("Загрузка данных"), "console:etl",
     _("Реестр источников, регламент, журнал"), Role.OPERATOR),
    ("quarantine", _("Карантин"), "console:quarantine",
     _("Записи, не прошедшие проверки"), Role.OPERATOR),
    ("quality", _("Качество данных"), "console:quality",
     _("Полнота и целостность записей"), Role.OPERATOR),
    ("users", _("Пользователи"), "console:users",
     _("Учётные записи и роли"), Role.ADMIN),
    ("references", _("Справочники"), "console:references",
     _("Округа, типы, категории, источники"), Role.ADMIN),
    ("feedback", _("Обращения"), "console:feedback",
     _("Обратная связь и ответы"), Role.ADMIN),
    ("content", _("Материалы"), "console:content",
     _("Аналитические публикации"), Role.ADMIN),
    ("audit", _("Журнал аудита"), "console:audit",
     _("Действия всех пользователей"), Role.ADMIN),
    ("system", _("Состояние среды"), "console:system",
     _("Параметры развёртывания"), Role.ADMIN),
)


def role_required(minimum: str):
    """Пропустить в раздел пользователя с ролью не ниже указанной."""

    def decorate(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            profile = profile_for(request.user)
            allowed = request.user.is_superuser or (profile and profile.has_role(minimum))
            if not allowed:
                raise PermissionDenied(
                    _("Раздел доступен с роли «%(role)s»") % {"role": Role(minimum).label}
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorate


#: Разделы ведения системы: учётные записи, справочники, содержание, журналы.
admin_required = role_required(Role.ADMIN)

#: Разделы работы с данными: загрузка, карантин, качество, верификация.
#: Диспетчер отвечает за состояние сведений, а не за устройство системы.
operator_required = role_required(Role.OPERATOR)


def _console_context(request, *, title: str, tab: str, lead: str = "", **extra) -> dict:
    """Общий контекст страниц панели.

    Перечень вкладок собирается по роли: диспетчер видит разделы работы
    с данными, администратор — их и разделы ведения системы.
    """
    profile = profile_for(request.user)
    administers = bool(profile and profile.can_administer)
    return page_context(
        request,
        title=title,
        lead=lead,
        active="console",
        crumbs=[
            (_("Панель администратора") if administers else _("Работа с данными"),
             "console:dashboard"),
            (title,),
        ],
        tabs=[row for row in CONSOLE_TABS if profile and profile.has_role(row[4])],
        current_tab=tab,
        can_administer=administers,
        pending_feedback=FeedbackMessage.objects.filter(
            status__in=[FeedbackMessage.Status.NEW, FeedbackMessage.Status.IN_WORK]
        ).count(),
        **extra,
    )


# ---------------------------------------------------------------------------
#  1. Обзор системы
# ---------------------------------------------------------------------------


@operator_required
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

    coverage = selectors.data_coverage()
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
        coverage=coverage,
        records_total=sum(row["count"] for row in coverage),
        quarantine_open=EtlReject.objects.filter(reviewed_at__isnull=True).count(),
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
            notify.role_changed(user, role=new_role, actor=request.user)
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


@operator_required
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
        {
            "title": _("Записи источников в карантине"),
            "detail": _("Не прошли проверку на входе и не попали в реестры"),
            "count": EtlReject.objects.filter(reviewed_at__isnull=True).count(),
            "total": EtlRun.objects.aggregate(
                total=Sum("records_loaded")
            )["total"] or 0,
            "severity": "warn",
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


@operator_required
def etl(request):
    """Реестр конвейеров, регламент и журнал загрузок."""
    queryset = EtlRun.objects.select_related("source", "actor").order_by("-started_at")

    status = choice_param(request, "status", ["running", "success", "partial", "failed"])
    source_id = int_param(request, "source")
    pipeline_name = request.GET.get("pipeline", "").strip()
    if status:
        queryset = queryset.filter(status=status)
    if source_id:
        queryset = queryset.filter(source_id=source_id)
    if pipeline_name in registry.names():
        queryset = queryset.filter(pipeline=pipeline_name)

    context = _console_context(
        request,
        title=_("Загрузка данных"),
        tab="etl",
        lead=_(
            "Состав источников, регламент обновления и хронология загрузок. "
            "Набор данных запускается отсюда; ход выполнения виден в журнале."
        ),
        page_obj=paginate(request, queryset, per_page=25),
        health=selectors.etl_health(limit=1),
        sources=DataSource.objects.all(),
        pipelines=_pipeline_rows(),
        run_form=PipelineRunForm(),
        queue_configured=dispatch.queue_configured(),
        quarantine_open=EtlReject.objects.filter(reviewed_at__isnull=True).count(),
        filters={"status": status, "source": source_id, "pipeline": pipeline_name},
    )
    return render(request, "console/etl.html", context)


def _pipeline_rows() -> list[dict]:
    """Реестр конвейеров вместе с регламентом и итогом последней загрузки."""
    latest = _latest_runs()
    rows = []
    for pipeline in registry.available():
        rows.append({
            "pipeline": pipeline,
            "schedule": schedule.describe(pipeline) if pipeline.frequency else "",
            "due": schedule.is_due(pipeline),
            "last": latest.get(pipeline.name),
            "checks": len(pipeline.checks),
        })
    return rows


def _latest_runs() -> dict:
    """Последняя загрузка каждого набора.

    Выборка идёт по одному запросу на набор: их меньше десятка, а выражение
    ``DISTINCT ON`` поддерживает не всякая СУБД.
    """
    result = {}
    for name in registry.names():
        entry = (
            EtlRun.objects.filter(pipeline=name)
            .order_by("-started_at")
            .values("pipeline", "status", "started_at", "records_loaded",
                    "records_unchanged", "records_errors")
            .first()
        )
        if entry:
            result[name] = entry
    return result


@require_POST
@operator_required
def etl_start(request):
    """Начать загрузку набора данных."""
    form = PipelineRunForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error.as_text())
        return redirect("console:etl")

    name = form.cleaned_data["pipeline"]
    try:
        submission = dispatch.submit(
            name,
            trigger=EtlTrigger.CONSOLE,
            actor=request.user,
            refresh=form.cleaned_data["refresh"],
            prune=form.cleaned_data["prune"],
        )
    except PipelineError as exc:
        messages.error(request, _("Загрузка прервана: %(reason)s") % {"reason": exc})
        request.audit_written = True
        return redirect("console:etl")

    if submission.deferred:
        messages.success(
            request,
            _("Загрузка «%(title)s» передана исполнителю. Итог появится "
              "в журнале по завершении.") % {"title": submission.pipeline.title},
        )
    else:
        report = submission.report
        messages.success(
            request,
            _("«%(title)s»: создано %(created)d, обновлено %(updated)d, "
              "без изменений %(unchanged)d, отклонено %(rejected)d.")
            % {
                "title": submission.pipeline.title,
                "created": report.created,
                "updated": report.updated,
                "unchanged": report.unchanged,
                "rejected": report.rejected,
            },
        )
    selectors.invalidate_caches()
    request.audit_written = True
    return redirect("console:etl")


@operator_required
def etl_run(request, pk: int):
    """Карточка одной загрузки: счётчики и отклонённые записи."""
    run = get_object_or_404(
        EtlRun.objects.select_related("source", "actor"), pk=pk
    )
    rejects = run.rejects.select_related("reviewed_by").order_by("id")

    context = _console_context(
        request,
        title=_("Загрузка № %(number)s") % {"number": run.pk},
        tab="etl",
        lead=_(
            "Итог одного прохождения конвейера: сколько записей поступило, "
            "что из них изменилось и что не прошло проверки."
        ),
        run=run,
        rejects=rejects[:200],
        rejects_total=rejects.count(),
        by_check=(
            run.rejects.values("check_code")
            .annotate(count=Count("id"))
            .order_by("-count")
        ),
    )
    return render(request, "console/etl_run.html", context)


@operator_required
def etl_upload(request):
    """Загрузка ряда из файла, присланного пользователем."""
    form = FlowUploadForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["file"]
        try:
            submission = dispatch.submit(
                "upload.flows",
                trigger=EtlTrigger.UPLOAD,
                actor=request.user,
                inline=True,
                content=uploaded.read(),
                filename=uploaded.name,
            )
        except PipelineError as exc:
            messages.error(request, _("Выгрузка не принята: %(reason)s") % {"reason": exc})
        else:
            report = submission.report
            messages.success(
                request,
                _("Принято строк: %(written)d, без изменений %(unchanged)d, "
                  "отклонено %(rejected)d.")
                % {
                    "written": report.written,
                    "unchanged": report.unchanged,
                    "rejected": report.rejected,
                },
            )
            request.audit_written = True
            if report.run_id:
                return redirect("console:etl_run", pk=report.run_id)
            return redirect("console:etl")

    context = _console_context(
        request,
        title=_("Выгрузка ряда"),
        tab="etl",
        lead=_(
            "Ряд, присланный файлом, проходит тот же конвейер, что и данные "
            "внешних служб: те же проверки, тот же журнал, тот же карантин."
        ),
        form=form,
        columns=COLUMNS,
    )
    return render(request, "console/etl_upload.html", context)


@operator_required
def etl_template(request):
    """Образец выгрузки для заполнения."""
    response = HttpResponse(template_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="freightflow-flows.csv"'
    return response


# ---------------------------------------------------------------------------
#  8. Карантин загрузки
# ---------------------------------------------------------------------------


@operator_required
def quarantine(request):
    """Записи источников, не прошедшие проверку качества.

    Карантин — рабочее место, а не отчёт: по нему видно, что именно в источнике
    подлежит исправлению. Разобранные записи отмечаются и уходят из очереди,
    оставаясь доступными для сверки.
    """
    queryset = EtlReject.objects.select_related("run", "run__source", "reviewed_by")

    check_code = request.GET.get("check", "").strip()
    pipeline_name = request.GET.get("pipeline", "").strip()
    state = choice_param(request, "state", ["open", "reviewed"]) or "open"

    if check_code:
        queryset = queryset.filter(check_code=check_code)
    if pipeline_name in registry.names():
        queryset = queryset.filter(run__pipeline=pipeline_name)
    if state == "open":
        queryset = queryset.filter(reviewed_at__isnull=True)
    else:
        queryset = queryset.filter(reviewed_at__isnull=False)

    context = _console_context(
        request,
        title=_("Карантин загрузки"),
        tab="quarantine",
        lead=_(
            "Записи, отклонённые проверками качества, вместе с причиной "
            "и положением в источнике. Доля отклонений — показатель "
            "состояния источника, а не работы системы."
        ),
        page_obj=paginate(request, queryset.order_by("-created_at", "id"), per_page=25),
        by_check=(
            EtlReject.objects.filter(reviewed_at__isnull=True)
            .values("check_code")
            .annotate(count=Count("id"))
            .order_by("-count")
        ),
        pipelines=registry.available(),
        open_total=EtlReject.objects.filter(reviewed_at__isnull=True).count(),
        reviewed_total=EtlReject.objects.filter(reviewed_at__isnull=False).count(),
        filters={"check": check_code, "pipeline": pipeline_name, "state": state},
    )
    return render(request, "console/quarantine.html", context)


@require_POST
@operator_required
def quarantine_action(request):
    """Отметить записи карантина разобранными либо вернуть их в очередь."""
    action = request.POST.get("action", "")
    identifiers = [int(value) for value in request.POST.getlist("reject") if value.isdigit()]
    scope = EtlReject.objects.filter(pk__in=identifiers)

    if action == "review":
        updated = scope.update(reviewed_at=timezone.now(), reviewed_by=request.user)
        messages.success(
            request, _("Отмечено разобранными: %(count)d") % {"count": updated}
        )
    elif action == "reopen":
        updated = scope.update(reviewed_at=None, reviewed_by=None)
        messages.success(
            request, _("Возвращено в очередь: %(count)d") % {"count": updated}
        )
    elif action == "review_check":
        code = request.POST.get("check", "")
        updated = EtlReject.objects.filter(
            check_code=code, reviewed_at__isnull=True
        ).update(reviewed_at=timezone.now(), reviewed_by=request.user)
        messages.success(
            request,
            _("Отмечено разобранными по проверке «%(code)s»: %(count)d")
            % {"code": code, "count": updated},
        )
    else:
        messages.error(request, _("Действие не распознано."))

    request.audit_written = True
    return redirect(request.POST.get("next") or "console:quarantine")


@require_POST
@operator_required
def cache_flush(request):
    """Сбросить кеш сводок и аналитических расчётов."""
    from analytics import services as analytics_services

    selectors.invalidate_caches()
    analytics_services.invalidate()
    messages.success(request, _("Кеш сводок и аналитических расчётов сброшен."))
    request.audit_written = True
    return redirect(request.POST.get("next") or "console:dashboard")


# ---------------------------------------------------------------------------
#  9. Журнал аудита
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
#  10. Состояние среды
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

"""Личный кабинет пользователя и регистрация.

Кабинет объединяет девять разделов: обзор, профиль, избранное, сохранённые
виды, наборы сравнения, центр выгрузок, подписки, уведомления и журнал
собственных действий. Доступ ко всем разделам требует авторизации; операции
выгрузки дополнительно ограничены ролью «Аналитик» и выше.
"""

from __future__ import annotations

from core.views.base import page_context, paginate
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from .forms import ProfileForm, RegistrationForm, SavedViewForm, SubscriptionForm
from .models import (
    AuditEvent,
    ComparisonSet,
    ExportJob,
    Favorite,
    IncidentSubscription,
    Notification,
    SavedView,
    profile_for,
)

#: Ключ сессии, через который только что выпущенный токен доходит до
#: страницы после перенаправления. Значение снимается при первом же показе.
FRESH_TOKEN_KEY = "fresh_api_token"

# Состав вкладок кабинета: код, подпись, маршрут. Используется общим
# шаблоном навигации, поэтому порядок разделов задаётся в одном месте.
CABINET_TABS: tuple[tuple[str, str, str], ...] = (
    ("overview", "Обзор", "accounts:overview"),
    ("profile", _("Профиль"), "accounts:profile"),
    ("favorites", _("Избранное"), "accounts:favorites"),
    ("views", _("Сохранённые виды"), "accounts:saved_views"),
    ("comparisons", _("Наборы сравнения"), "accounts:comparisons"),
    ("exports", _("Центр выгрузок"), "accounts:exports"),
    ("subscriptions", _("Подписки"), "accounts:subscriptions"),
    ("notifications", _("Уведомления"), "accounts:notifications"),
    ("activity", _("Журнал действий"), "accounts:activity"),
    ("api", _("Доступ к API"), "accounts:api_access"),
)


def _cabinet_context(request, *, title: str, tab: str, lead: str = "", **extra) -> dict:
    """Собрать контекст страницы кабинета вместе с составом вкладок."""
    context = page_context(
        request,
        title=title,
        lead=lead,
        active="account",
        crumbs=[(_("Личный кабинет"), "accounts:overview"), (title,)],
        tabs=CABINET_TABS,
        current_tab=tab,
        profile=profile_for(request.user),
        unread=Notification.objects.filter(user=request.user, is_read=False).count(),
        **extra,
    )
    return context


def register(request):
    """Самостоятельная регистрация пользователя с ролью «Наблюдатель»."""
    if request.user.is_authenticated:
        return redirect("accounts:overview")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(
                request,
                _("Учётная запись создана. Роль «Наблюдатель» назначена по умолчанию; "
                "для расширения прав обратитесь к администратору системы."),
            )
            return redirect("accounts:overview")
    else:
        form = RegistrationForm()

    context = page_context(
        request,
        title=_("Регистрация"),
        lead=_("Создание учётной записи для работы с личным кабинетом системы."),
        active="account",
        crumbs=[(_("Регистрация"),)],
        form=form,
    )
    return render(request, "registration/register.html", context)


@login_required
def overview(request):
    """Сводка личного кабинета."""
    exports = ExportJob.objects.filter(user=request.user)
    context = _cabinet_context(
        request,
        title=_("Обзор"),
        tab="overview",
        lead=_("Состояние личного кабинета и последние действия в системе."),
        counters={
            "favorites": Favorite.objects.filter(user=request.user).count(),
            "views": SavedView.objects.filter(user=request.user).count(),
            "comparisons": ComparisonSet.objects.filter(user=request.user).count(),
            "exports": exports.count(),
            "subscriptions": IncidentSubscription.objects.filter(
                user=request.user, is_active=True
            ).count(),
        },
        recent_views=SavedView.objects.filter(user=request.user)[:5],
        recent_favorites=Favorite.objects.filter(user=request.user)[:5],
        recent_exports=exports[:5],
        recent_events=AuditEvent.objects.filter(user=request.user)[:8],
        notifications=Notification.objects.filter(user=request.user, is_read=False)[:5],
    )
    return render(request, "account/overview.html", context)


@login_required
def profile(request):
    """Просмотр и редактирование профиля."""
    user_profile = profile_for(request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user_profile, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Сведения профиля обновлены."))
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=user_profile, user=request.user)

    context = _cabinet_context(
        request,
        title=_("Профиль"),
        tab="profile",
        lead=_("Контактные сведения, роль и предпочтения интерфейса."),
        form=form,
    )
    return render(request, "account/profile.html", context)


@login_required
def favorites(request):
    """Список закладок пользователя."""
    queryset = Favorite.objects.filter(user=request.user)
    kind = request.GET.get("kind", "")
    if kind:
        queryset = queryset.filter(kind=kind)

    context = _cabinet_context(
        request,
        title=_("Избранное"),
        tab="favorites",
        lead=_("Закладки на объекты, округа, участки сети и маршруты."),
        page_obj=paginate(request, queryset, per_page=20),
        kinds=Favorite.Kind.choices,
        by_kind=list(queryset.values("kind").annotate(count=Count("id"))),
        selected_kind=kind,
    )
    return render(request, "account/favorites.html", context)


@require_POST
@login_required
def favorite_toggle(request):
    """Добавить или убрать закладку.

    Обработчик рассчитан на вызов из карточек объектов: повторное обращение с
    теми же параметрами снимает ранее поставленную закладку.
    """
    kind = request.POST.get("kind", "")
    target_id = request.POST.get("target_id", "")
    title = request.POST.get("title", "")[:250]
    next_url = request.POST.get("next", "/")

    if kind not in dict(Favorite.Kind.choices) or not target_id.isdigit():
        messages.error(request, _("Не удалось изменить закладку: некорректные параметры."))
        return redirect(next_url)

    existing = Favorite.objects.filter(user=request.user, kind=kind, target_id=target_id).first()
    if existing:
        existing.delete()
        messages.info(request, _("Закладка убрана."))
    else:
        Favorite.objects.create(
            user=request.user, kind=kind, target_id=int(target_id), title=title
        )
        messages.success(request, _("Добавлено в избранное."))
    request.audit_written = True
    return redirect(next_url)


@login_required
def saved_views(request):
    """Сохранённые условия отбора."""
    if request.method == "POST":
        form = SavedViewForm(request.POST)
        if form.is_valid():
            view = form.save(commit=False)
            view.user = request.user
            view.save()
            messages.success(request, _("Вид сохранён."))
            return redirect("accounts:saved_views")
    else:
        form = SavedViewForm(
            initial={
                "page": request.GET.get("page_key", ""),
                "query": request.GET.get("query", ""),
            }
        )

    context = _cabinet_context(
        request,
        title=_("Сохранённые виды"),
        tab="views",
        lead=_(
            "Настроенные условия отбора. Сохраняются параметры, а не данные: "
            "при открытии выборка выполняется заново."
        ),
        page_obj=paginate(request, SavedView.objects.filter(user=request.user), per_page=15),
        form=form,
    )
    return render(request, "account/saved_views.html", context)


@require_POST
@login_required
def saved_view_action(request, pk: int):
    """Операции над сохранённым видом: публикация, снятие, удаление."""
    view = get_object_or_404(SavedView, pk=pk, user=request.user)
    action = request.POST.get("action", "")

    if action == "publish":
        view.publish()
        messages.success(request, _("Вид опубликован — доступ открыт по ссылке."))
    elif action == "unpublish":
        view.unpublish()
        messages.info(request, _("Публичный доступ к виду закрыт."))
    elif action == "delete":
        view.delete()
        messages.info(request, _("Вид удалён."))
    request.audit_written = True
    return redirect("accounts:saved_views")


def shared_view(request, token: str):
    """Открыть сохранённый вид по публичной ссылке.

    Страница доступна без авторизации: автор вида явно разрешил доступ,
    а сами данные являются открытыми.
    """
    view = get_object_or_404(SavedView, share_token=token, is_public=True)
    view.register_open()
    return redirect(view.url)


@login_required
def comparisons(request):
    """Наборы объектов, отобранных для сравнения."""
    context = _cabinet_context(
        request,
        title=_("Наборы сравнения"),
        tab="comparisons",
        lead=_("Группы округов, объектов и маршрутов для сопоставительного анализа."),
        page_obj=paginate(request, ComparisonSet.objects.filter(user=request.user), per_page=15),
    )
    return render(request, "account/comparisons.html", context)


@login_required
def exports(request):
    """Центр выгрузок: перечень сформированных отчётов."""
    queryset = ExportJob.objects.filter(user=request.user)
    totals = queryset.aggregate(size=Sum("file_size"), rows=Sum("row_count"))

    context = _cabinet_context(
        request,
        title=_("Центр выгрузок"),
        tab="exports",
        lead=(
            "Сформированные отчёты в форматах XLSX, DOCX, CSV, PDF и GeoJSON. "
            f"Файлы хранятся {settings.EXPORT_RETENTION_DAYS} суток."
        ),
        page_obj=paginate(request, queryset, per_page=20),
        total_size=totals["size"] or 0,
        total_rows=totals["rows"] or 0,
        can_export=profile_for(request.user).can_export,
        retention=settings.EXPORT_RETENTION_DAYS,
    )
    return render(request, "account/exports.html", context)


@login_required
def export_download(request, pk: int):
    """Выдать пользователю ранее сформированный файл отчёта."""
    job = get_object_or_404(ExportJob, pk=pk, user=request.user)
    if not job.is_ready:
        raise Http404(_("Файл отчёта ещё не сформирован"))

    path = settings.EXPORT_ROOT / job.file_name
    if not path.exists():
        raise Http404(_("Файл отчёта удалён по истечении срока хранения"))

    return FileResponse(path.open("rb"), as_attachment=True, filename=job.file_name)


@login_required
def subscriptions(request):
    """Подписки на дорожные события."""
    if request.method == "POST":
        form = SubscriptionForm(request.POST)
        if form.is_valid():
            subscription = form.save(commit=False)
            subscription.user = request.user
            subscription.save()
            messages.success(request, _("Подписка оформлена."))
            return redirect("accounts:subscriptions")
    else:
        form = SubscriptionForm()

    context = _cabinet_context(
        request,
        title=_("Подписки"),
        tab="subscriptions",
        lead=_(
            "Оповещение о дорожных событиях в выбранном округе с заданным "
            "порогом серьёзности."
        ),
        items=IncidentSubscription.objects.filter(user=request.user).select_related("district"),
        form=form,
    )
    return render(request, "account/subscriptions.html", context)


@require_POST
@login_required
def subscription_delete(request, pk: int):
    """Удалить подписку."""
    get_object_or_404(IncidentSubscription, pk=pk, user=request.user).delete()
    messages.info(request, _("Подписка удалена."))
    request.audit_written = True
    return redirect("accounts:subscriptions")


@login_required
def notifications(request):
    """Уведомления пользователя."""
    queryset = Notification.objects.filter(user=request.user)
    if request.GET.get("unread") == "1":
        queryset = queryset.filter(is_read=False)

    context = _cabinet_context(
        request,
        title=_("Уведомления"),
        tab="notifications",
        lead=_("Сообщения системы о событиях, подпадающих под ваши подписки."),
        page_obj=paginate(request, queryset, per_page=20),
        only_unread=request.GET.get("unread") == "1",
    )
    return render(request, "account/notifications.html", context)


@require_POST
@login_required
def notifications_read(request):
    """Отметить все уведомления прочитанными."""
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    messages.info(request, _("Все уведомления отмечены как прочитанные."))
    request.audit_written = True
    return redirect("accounts:notifications")


@login_required
def activity(request):
    """Журнал собственных действий пользователя."""
    queryset = AuditEvent.objects.filter(user=request.user)
    action = request.GET.get("action", "")
    if action:
        queryset = queryset.filter(action=action)

    context = _cabinet_context(
        request,
        title=_("Журнал действий"),
        tab="activity",
        lead=_("Хронология входов, изменений и выгрузок в разрезе вашей учётной записи."),
        page_obj=paginate(request, queryset, per_page=25),
        actions=AuditEvent.Action.choices,
        selected_action=action,
    )
    return render(request, "account/activity.html", context)


@login_required
def api_access(request):
    """Управление персональным токеном доступа к REST API."""
    user_profile = profile_for(request.user)

    if request.method == "POST":
        if not user_profile.can_export:
            messages.error(
                request,
                _("Доступ к программному интерфейсу предоставляется с роли «Аналитик»."),
            )
        elif request.POST.get("action") == "issue":
            # Значение показывается один раз: в базе остаётся отпечаток,
            # и восстановить токен для повторного показа неоткуда.
            request.session[FRESH_TOKEN_KEY] = user_profile.issue_api_token()
            messages.success(
                request, _("Токен выпущен. Скопируйте его — повторно он не показывается.")
            )
        elif request.POST.get("action") == "revoke":
            user_profile.revoke_api_token()
            messages.info(request, _("Токен отозван."))
        request.audit_written = True
        return redirect("accounts:api_access")

    context = _cabinet_context(
        request,
        title=_("Доступ к API"),
        tab="api",
        lead=_(
            "Персональный токен для программного обращения к системе. "
            "Передаётся в заголовке Authorization."
        ),
        fresh_token=request.session.pop(FRESH_TOKEN_KEY, ""),
        token_prefix=user_profile.api_token_prefix,
        has_token=user_profile.has_api_token,
        issued_at=user_profile.api_token_created,
        used_at=user_profile.api_token_used,
        can_use=user_profile.can_export,
        base_url=request.build_absolute_uri("/api/v1/"),
    )
    return render(request, "account/api_access.html", context)

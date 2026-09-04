"""Представление, обслуживающее запросы на выгрузку отчётов."""

from __future__ import annotations

from accounts.models import ExportJob, profile_for
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from . import service


@login_required
@require_POST
def create(request):
    """Сформировать отчёт по набору данных и условиям отбора.

    Метод POST выбран не формально. Обработчик создаёт запись задания, пишет
    файл на диск и оставляет запись в журнале аудита; по ссылке такие действия
    выполняться не должны — их запускает предзагрузка ссылок браузером и обход
    роботом, и они проходят мимо защиты от подделки запросов.

    Условия отбора приходят полем ``filters`` — это строка запроса страницы,
    с которой вызвана выгрузка. Благодаря этому пользователь получает ровно
    ту выборку, которую видит на экране.
    """
    profile = profile_for(request.user)
    if not profile or not profile.can_export:
        raise PermissionDenied(
            "Выгрузка отчётов доступна пользователям с ролью «Аналитик» и выше"
        )

    back = request.POST.get("next") or "/"
    # Условия отбора разбираются как строка запроса: построители наборов
    # рассчитаны на QueryDict и умеют работать с повторяющимися параметрами.
    filters = QueryDict(request.POST.get("filters", ""))

    try:
        job = service.perform(
            request.user,
            request.POST.get("dataset", ""),
            request.POST.get("format", "xlsx").lower(),
            filters,
            path=request.path,
            request_id=getattr(request, "request_id", ""),
        )
    except service.ExportRequestError as error:
        messages.error(request, str(error))
        return redirect(back)

    request.audit_written = True

    if job.status == ExportJob.Status.FAILED:
        messages.error(
            request,
            "Не удалось сформировать отчёт. Сведения об ошибке записаны в журнал; "
            "попробуйте уменьшить объём выборки.",
        )
        return redirect(back)

    messages.success(
        request,
        f"Отчёт сформирован: {job.row_count} записей, {job.size_human}. "
        "Файл доступен в центре выгрузок.",
    )
    return redirect("accounts:exports")

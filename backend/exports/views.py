"""Представление, обслуживающее запросы на выгрузку отчётов."""

from __future__ import annotations

import logging

from accounts.models import AuditEvent, ExportJob, profile_for
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils import timezone

from .builders import BUILDERS, build_filename, build_geojson, ensure_export_root
from .datasets import DATASET_TITLES, DATASETS

logger = logging.getLogger("freightflow.exports")


@login_required
def create(request):
    """Сформировать отчёт по набору данных и условиям отбора.

    Условия отбора берутся из параметров запроса — тех же, что и на странице,
    с которой вызвана выгрузка. Благодаря этому пользователь получает ровно
    ту выборку, которую видит на экране.
    """
    profile = profile_for(request.user)
    if not profile or not profile.can_export:
        raise PermissionDenied(
            "Выгрузка отчётов доступна пользователям с ролью «Аналитик» и выше"
        )

    dataset_code = request.GET.get("dataset", "")
    fmt = request.GET.get("format", "xlsx").lower()
    back = request.GET.get("next") or "/"

    if dataset_code not in DATASETS:
        messages.error(request, "Указан неизвестный набор данных для выгрузки.")
        return redirect(back)
    if fmt not in BUILDERS and fmt != "geojson":
        messages.error(request, "Указан неподдерживаемый формат выгрузки.")
        return redirect(back)

    builder, geometry_getter = DATASETS[dataset_code]
    if fmt == "geojson" and geometry_getter is None:
        messages.error(
            request,
            "Набор данных не содержит геометрии и не может быть выгружен в GeoJSON.",
        )
        return redirect(back)

    job = ExportJob.objects.create(
        user=request.user,
        title=DATASET_TITLES.get(dataset_code, dataset_code),
        dataset=dataset_code,
        fmt=fmt,
        query=request.GET.urlencode()[:1000],
    )

    try:
        dataset = builder(request.GET)
        root = ensure_export_root()
        file_name = build_filename(dataset, fmt)
        path = root / file_name

        if fmt == "geojson":
            row_count = build_geojson(dataset, path, geometry_getter)
        else:
            row_count = BUILDERS[fmt](dataset, path)

        job.file_name = file_name
        job.file_size = path.stat().st_size
        job.row_count = row_count
        job.status = ExportJob.Status.DONE
        job.finished_at = timezone.now()
        job.save()

        AuditEvent.objects.create(
            user=request.user,
            action=AuditEvent.Action.EXPORT,
            entity="export",
            entity_id=str(job.pk),
            summary=f"Выгрузка «{job.title}» в формате {fmt.upper()}: {row_count} записей",
            path=request.path,
            request_id=getattr(request, "request_id", ""),
        )
        request.audit_written = True

        messages.success(
            request,
            f"Отчёт сформирован: {row_count} записей, {job.size_human}. "
            "Файл доступен в центре выгрузок.",
        )
    except Exception as exc:  # pragma: no cover — аварийная ветка
        logger.exception("Ошибка формирования отчёта %s (%s)", dataset_code, fmt)
        job.status = ExportJob.Status.FAILED
        job.error_message = str(exc)[:500]
        job.finished_at = timezone.now()
        job.save()
        messages.error(
            request,
            "Не удалось сформировать отчёт. Сведения об ошибке записаны в журнал; "
            "попробуйте уменьшить объём выборки.",
        )
        return redirect(back)

    return redirect("accounts:exports")

"""Формирование отчётного документа.

Порядок один для всех способов обращения: страница системы и программный
интерфейс создают запись задания, пишут файл и оставляют след в журнале
одним и тем же кодом. Расходятся они только в том, как сообщают о неудаче:
человеку — сообщением на странице, программе — кодом ответа.
"""

from __future__ import annotations

import logging

from accounts.models import AuditEvent, ExportJob
from django.utils import timezone

from .builders import BUILDERS, build_filename, build_geojson, ensure_export_root
from .datasets import DATASET_TITLES, DATASETS

logger = logging.getLogger("freightflow.exports")

#: Форматы, доступные к выгрузке: табличные построители и пространственный слой.
FORMATS: tuple[str, ...] = (*BUILDERS.keys(), "geojson")

#: Тип содержимого готового файла — нужен при отдаче документа потоком.
CONTENT_TYPES: dict[str, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "geojson": "application/geo+json",
}


class ExportRequestError(ValueError):
    """Запрос на выгрузку составлен неверно и выполнен быть не может."""


def validate(dataset_code: str, fmt: str) -> None:
    """Проверить, что набор и формат существуют и совместимы."""
    if dataset_code not in DATASETS:
        raise ExportRequestError("Указан неизвестный набор данных для выгрузки.")
    if fmt not in FORMATS:
        raise ExportRequestError("Указан неподдерживаемый формат выгрузки.")
    if fmt == "geojson" and DATASETS[dataset_code][1] is None:
        raise ExportRequestError(
            "Набор данных не содержит геометрии и не может быть выгружен в GeoJSON."
        )


def perform(user, dataset_code: str, fmt: str, filters, *, path: str = "",
            request_id: str = "") -> ExportJob:
    """Сформировать документ и вернуть запись задания.

    Задание создаётся до начала работы: неудачная выгрузка тоже должна
    остаться в центре выгрузок вместе с причиной, иначе пользователь видит
    лишь то, что файл не появился.
    """
    validate(dataset_code, fmt)
    builder, geometry_getter = DATASETS[dataset_code]

    job = ExportJob.objects.create(
        user=user,
        title=DATASET_TITLES.get(dataset_code, dataset_code),
        dataset=dataset_code,
        fmt=fmt,
        query=filters.urlencode()[:1000],
    )

    try:
        dataset = builder(filters)
        file_name = build_filename(dataset, fmt)
        target = ensure_export_root() / file_name

        if fmt == "geojson":
            row_count = build_geojson(dataset, target, geometry_getter)
        else:
            row_count = BUILDERS[fmt](dataset, target)

        job.file_name = file_name
        job.file_size = target.stat().st_size
        job.row_count = row_count
        job.status = ExportJob.Status.DONE
        job.finished_at = timezone.now()
        job.save()
    except Exception as exc:  # pragma: no cover — аварийная ветка
        logger.exception("Ошибка формирования отчёта %s (%s)", dataset_code, fmt)
        job.status = ExportJob.Status.FAILED
        job.error_message = str(exc)[:500]
        job.finished_at = timezone.now()
        job.save()
        return job

    AuditEvent.objects.create(
        user=user,
        action=AuditEvent.Action.EXPORT,
        entity="export",
        entity_id=str(job.pk),
        summary=f"Выгрузка «{job.title}» в формате {fmt.upper()}: {row_count} записей",
        path=path,
        request_id=request_id,
    )
    return job

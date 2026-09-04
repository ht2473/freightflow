"""Загрузка справочных наборов, ведущихся вручную.

Примеры::

    python manage.py load_reference               # все наборы
    python manage.py load_reference --only frame  # только грузовой каркас

Наборы каталога ``data/reference`` не публикуются машиночитаемо и потому
входят в поставку проекта файлами. Команда переносит их в базу наравне
с данными внешних служб.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from etl import reference
from etl.osm.loaders import log_run

#: Наборы и обслуживающие их процедуры.
DATASETS = {
    "frame": (reference.load_freight_frame, "road_segments"),
}


class Command(BaseCommand):
    help = "Загрузить справочные наборы, ведущиеся вручную"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--only",
            choices=sorted(DATASETS),
            action="append",
            dest="datasets",
            help="Загрузить только указанный набор; допускается повторение",
        )

    def handle(self, *args, **options) -> None:
        source = reference.ensure_source()
        selected = options["datasets"] or list(DATASETS)

        for name in [key for key in DATASETS if key in selected]:
            procedure, target_table = DATASETS[name]
            started_at = timezone.now()
            try:
                report = procedure()
            except reference.ReferenceError as exc:
                raise CommandError(str(exc)) from exc

            log_run(report, source, target_table, started_at)

            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{report.dataset}"))
            self.stdout.write(f"  записей в наборе : {report.fetched}")
            self.stdout.write(f"  отмечено         : {report.updated}")
            self.stdout.write(f"  не сопоставлено  : {report.skipped}")
            for note in report.notes:
                self.stdout.write(self.style.WARNING(f"  ! {note}"))

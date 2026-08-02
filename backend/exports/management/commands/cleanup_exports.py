"""Удаление устаревших файлов отчётов.

Файлы выгрузок хранятся ограниченное время: они воспроизводимы повторным
запуском и не являются первичными данными. Команда предназначена для
регламентного запуска (cron либо системный таймер) и удаляет как файлы, так и
записи о заданиях, срок хранения которых истёк.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from accounts.models import ExportJob
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    """Очистить каталог выгрузок от устаревших файлов."""

    help = "Удаление файлов отчётов, срок хранения которых истёк"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=settings.EXPORT_RETENTION_DAYS,
            help="Срок хранения в сутках",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что было бы удалено, не удаляя файлы",
        )

    def handle(self, *args, **options) -> None:
        days = options["days"]
        dry_run = options["dry_run"]
        threshold = timezone.now() - timedelta(days=days)
        root = Path(settings.EXPORT_ROOT)

        expired = ExportJob.objects.filter(created_at__lt=threshold)
        removed_files = freed_bytes = 0

        for job in expired:
            if job.file_name:
                path = root / job.file_name
                if path.exists():
                    freed_bytes += path.stat().st_size
                    if not dry_run:
                        path.unlink()
                    removed_files += 1

        removed_jobs = expired.count()
        if not dry_run:
            expired.delete()

        # Файлы без соответствующей записи в базе — следствие аварийного
        # завершения формирования; они также подлежат удалению.
        orphans = 0
        if root.exists():
            known = set(ExportJob.objects.values_list("file_name", flat=True))
            for path in root.iterdir():
                if not path.is_file() or path.name in known:
                    continue
                age = timezone.now() - timezone.datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.get_current_timezone()
                )
                if age > timedelta(days=days):
                    freed_bytes += path.stat().st_size
                    if not dry_run:
                        path.unlink()
                    orphans += 1

        prefix = "Было бы удалено" if dry_run else "Удалено"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: заданий {removed_jobs}, файлов {removed_files}"
                + (f", осиротевших файлов {orphans}" if orphans else "")
                + f", освобождено {freed_bytes / 1024 / 1024:.1f} МБ"
            )
        )

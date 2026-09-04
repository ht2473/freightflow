"""Запуск конвейеров загрузки данных.

Примеры::

    python manage.py etl --list                    # состав реестра конвейеров
    python manage.py etl --all                     # все наборы по порядку
    python manage.py etl osm.districts osm.objects # выбранные наборы
    python manage.py etl --all --refresh           # заново обратиться к службам
    python manage.py etl --all --offline           # только по сохранённым ответам
    python manage.py etl osm.objects --prune       # привести реестр к источнику
    python manage.py etl --due                     # то, что подошло по регламенту
    python manage.py etl upload.flows --file ряд.csv

Порядок наборов задаётся реестром, а не порядком ключей в строке запуска:
объект относится к округу по координатам, зона строится по геометрии кольцевой
магистрали, принадлежность каркасу отмечается в уже заполненном реестре.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from etl import registry, schedule
from etl.pipeline import Context, PipelineError, RunReport, run


class Command(BaseCommand):
    help = "Загрузить данные из внешних источников через конвейер"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "pipelines",
            nargs="*",
            help="Обозначения конвейеров; без них требуется --all или --due",
        )
        parser.add_argument(
            "--list", action="store_true", dest="show_list",
            help="Показать реестр конвейеров и выйти",
        )
        parser.add_argument("--all", action="store_true", help="Все наборы по порядку")
        parser.add_argument(
            "--due", action="store_true",
            help="Только наборы, у которых подошёл регламентный срок обновления",
        )
        parser.add_argument(
            "--refresh", action="store_true",
            help="Не использовать сохранённые ответы, обратиться к источнику заново",
        )
        parser.add_argument(
            "--offline", action="store_true",
            help="Работать только по сохранённым ответам и отказать при их отсутствии",
        )
        parser.add_argument(
            "--prune", action="store_true",
            help="Удалить записи, отсутствующие в выгрузке: привести реестр к источнику",
        )
        parser.add_argument(
            "--dry-run", action="store_true", dest="dry_run",
            help="Пройти конвейер без записи: показать, что изменилось бы",
        )
        parser.add_argument(
            "--file", dest="upload",
            help="Файл выгрузки для конвейеров, работающих с присланными данными",
        )

    def handle(self, *args, **options) -> None:
        if options["show_list"]:
            self._show_registry()
            return

        if options["refresh"] and options["offline"]:
            raise CommandError(
                "Ключи --refresh и --offline исключают друг друга: первый требует "
                "обращения к источнику, второй его запрещает."
            )

        selected = self._select(options)
        context = Context(
            refresh=options["refresh"],
            offline=options["offline"],
            prune=options["prune"],
            dry_run=options["dry_run"],
            options={"file": options["upload"]} if options["upload"] else {},
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(
                "Проверочный проход: записи в базу не вносятся"
            ))

        for pipeline in selected:
            try:
                report = run(pipeline, context)
            except PipelineError as exc:
                raise CommandError(f"{pipeline.title}: {exc}") from exc
            self._print(report)

    # ------------------------------------------------------------------ отбор

    def _select(self, options) -> list:
        """Определить состав конвейеров по ключам запуска."""
        if options["due"]:
            due = schedule.due()
            if not due:
                self.stdout.write("Регламентный срок не подошёл ни у одного набора")
            return due

        if options["all"]:
            # Конвейеры, работающие с присланным файлом, в общий проход
            # не входят: загружать им нечего, пока файла нет.
            return [item for item in registry.available() if not item.expects_upload]

        if not options["pipelines"]:
            raise CommandError(
                "Укажите конвейеры, либо --all, либо --due. "
                "Состав реестра: python manage.py etl --list"
            )

        chosen = set(options["pipelines"])
        unknown = chosen - set(registry.names())
        if unknown:
            raise CommandError(
                f"Неизвестные конвейеры: {', '.join(sorted(unknown))}. "
                f"Объявлены: {', '.join(registry.names())}"
            )
        # Порядок берётся из реестра: между наборами есть зависимости.
        return [item for item in registry.available() if item.name in chosen]

    # ---------------------------------------------------------------- вывод

    def _show_registry(self) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Конвейеры загрузки"))
        for pipeline in registry.available():
            self.stdout.write(f"\n  {pipeline.name}  —  {pipeline.title}")
            self.stdout.write(f"      таблица   : {pipeline.target_table}")
            self.stdout.write(f"      источник  : {pipeline.source_code}")
            if pipeline.frequency:
                self.stdout.write(f"      регламент : {schedule.describe(pipeline)}")
            if pipeline.description:
                self.stdout.write(f"      {pipeline.description}")
            if pipeline.checks:
                self.stdout.write("      проверки  :")
                for check in pipeline.checks:
                    self.stdout.write(f"          {check.code:24s} {check.title}")

    def _print(self, report: RunReport | None) -> None:
        if report is None:
            return
        origin = "из кеша" if report.from_cache else "из источника"
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{report.title} ({origin})"))
        self.stdout.write(f"  получено элементов : {report.fetched}")
        self.stdout.write(f"  создано записей    : {report.created}")
        self.stdout.write(f"  обновлено записей  : {report.updated}")
        self.stdout.write(f"  без изменений      : {report.unchanged}")
        self.stdout.write(f"  отклонено          : {report.rejected}")
        self.stdout.write(f"  отсеяно при отборе : {report.filtered}")
        if report.removed:
            self.stdout.write(f"  удалено из реестра : {report.removed}")

        if report.by_check:
            self.stdout.write("  отклонено проверками:")
            for code, count in report.by_check.most_common():
                self.stdout.write(f"      {code:28s} {count}")

        if report.by_rule:
            self.stdout.write("  отсеяно при отборе:")
            for rule, count in report.by_rule.most_common(10):
                self.stdout.write(f"      {rule:28s} {count}")

        for detail in report.details:
            self.stdout.write(f"  · {detail}")
        for note in report.notes[:10]:
            self.stdout.write(self.style.WARNING(f"  ! {note}"))
        if len(report.notes) > 10:
            self.stdout.write(f"  ... ещё замечаний: {len(report.notes) - 10}")

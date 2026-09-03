"""Загрузка данных OpenStreetMap в реестры системы.

Примеры::

    python manage.py load_osm                  # округа, затем объекты
    python manage.py load_osm --only districts # только границы округов
    python manage.py load_osm --refresh        # заново обратиться к источнику
    python manage.py load_osm --offline        # только по сохранённым ответам

Порядок наборов существен: объект относится к округу по координатам, поэтому
границы округов должны быть заполнены раньше. При запуске без ограничений
команда соблюдает этот порядок сама.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from etl.client import OverpassClient, OverpassError
from etl.osm import loaders

#: Наборы данных и обслуживающие их процедуры, в порядке загрузки.
DATASETS: dict[str, tuple[str, str]] = {
    "districts": ("load_districts", "districts"),
    "objects": ("load_infrastructure", "infrastructure_objects"),
}


class Command(BaseCommand):
    help = "Загрузить данные OpenStreetMap: границы округов и объекты инфраструктуры"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--only",
            choices=sorted(DATASETS),
            action="append",
            dest="datasets",
            help="Загрузить только указанный набор; допускается повторение",
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Не использовать сохранённые ответы, обратиться к источнику заново",
        )
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Работать только по сохранённым ответам и отказать при их отсутствии",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "Удалить из реестра записи, отсутствующие в выгрузке: привести "
                "его к состоянию источника"
            ),
        )

    def handle(self, *args, **options) -> None:
        if options["refresh"] and options["offline"]:
            raise CommandError(
                "Ключи --refresh и --offline исключают друг друга: первый требует "
                "обращения к источнику, второй его запрещает."
            )

        client = OverpassClient(offline=options["offline"])
        source = loaders.ensure_source()

        created_types = loaders.ensure_types()
        if created_types:
            self.stdout.write(f"Справочник типов дополнен: {created_types} записей")

        selected = options["datasets"] or list(DATASETS)
        # Порядок задаётся составом DATASETS, а не порядком ключей в строке
        # запуска: объекты относятся к округам по координатам.
        ordered = [name for name in DATASETS if name in selected]

        for name in ordered:
            procedure, target_table = DATASETS[name]
            started_at = timezone.now()
            try:
                procedure_call = getattr(loaders, procedure)
                arguments = {"refresh": options["refresh"]}
                # Приведение к составу источника поддерживают не все наборы:
                # округа образуют закрытый справочник и удалению не подлежат.
                if "prune" in procedure_call.__code__.co_varnames:
                    arguments["prune"] = options["prune"]
                report = procedure_call(client, **arguments)
            except OverpassError as exc:
                raise CommandError(str(exc)) from exc
            except RuntimeError as exc:
                raise CommandError(str(exc)) from exc

            loaders.log_run(report, source, target_table, started_at)
            self._print(report)

    def _print(self, report: loaders.LoadReport) -> None:
        origin = "из кеша" if report.from_cache else "из источника"
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{report.dataset} ({origin})"))
        self.stdout.write(f"  получено элементов : {report.fetched}")
        self.stdout.write(f"  создано записей    : {report.created}")
        self.stdout.write(f"  обновлено записей  : {report.updated}")
        self.stdout.write(f"  отклонено          : {report.skipped}")
        self.stdout.write(f"  без привязки       : {report.unlocated}")
        if report.removed:
            self.stdout.write(f"  удалено из реестра : {report.removed}")

        if report.rejected_by_rule:
            self.stdout.write("  отклонено по правилам:")
            for rule, count in sorted(
                report.rejected_by_rule.items(), key=lambda item: -item[1]
            ):
                self.stdout.write(f"      {rule:24s} {count}")

        for note in report.notes[:10]:
            self.stdout.write(self.style.WARNING(f"  ! {note}"))
        if len(report.notes) > 10:
            self.stdout.write(f"  ... ещё замечаний: {len(report.notes) - 10}")

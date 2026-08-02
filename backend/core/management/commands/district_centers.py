"""Заполнение координат центров административных округов.

Исходный набор данных содержит только атрибутивную часть справочника округов.
Координаты условных центров необходимы карте: по ним размещаются подписи
округов и агрегированные показатели слоя «Округа». Значения соответствуют
географическому центру застроенной части округа по данным портала открытых
данных города Москвы.

Команда идемпотентна и работает на обоих поддерживаемых контурах — запись
выполняется средствами ORM, а не вызовом функций PostGIS.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import District

# Аббревиатура округа → (долгота, широта) в системе WGS-84.
CENTERS: dict[str, tuple[float, float]] = {
    "ЦАО": (37.6208, 55.7539),
    "САО": (37.5350, 55.8386),
    "СВАО": (37.6205, 55.8635),
    "ВАО": (37.7754, 55.7877),
    "ЮВАО": (37.7541, 55.6924),
    "ЮАО": (37.6541, 55.6216),
    "ЮЗАО": (37.5762, 55.6624),
    "ЗАО": (37.4435, 55.7286),
    "СЗАО": (37.4380, 55.8290),
    "ЗелАО": (37.2143, 55.9917),
    "НАО": (37.2100, 55.5570),
    "ТАО": (37.1200, 55.4297),
}


class Command(BaseCommand):
    """Проставить координаты центров округов."""

    help = "Заполнение поля center справочника округов"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Перезаписать уже заполненные координаты",
        )

    def handle(self, *args, **options) -> None:
        from geo import Geometry

        updated = skipped = missing = 0
        for district in District.objects.all():
            coordinates = CENTERS.get(district.short_name)
            if coordinates is None:
                missing += 1
                self.stderr.write(f"  координаты не заданы для округа {district.short_name}")
                continue
            if district.center is not None and not options["force"]:
                skipped += 1
                continue
            district.center = Geometry.point(*coordinates)
            district.save(update_fields=["center"])
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Координаты центров: обновлено {updated}, пропущено {skipped}"
                + (f", без данных {missing}" if missing else "")
            )
        )

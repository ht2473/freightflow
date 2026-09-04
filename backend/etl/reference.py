"""Загрузка справочных наборов, которые не публикуются машиночитаемо.

Часть сведений предметной области существует только в виде схем и печатных
перечней: грузовой каркас Москвы Департамент транспорта публикует схемой,
ряды статистики Росстат — таблицами в изданиях. Выгрузки, пригодной для
автоматической загрузки, у них нет.

Такие сведения ведутся в проекте файлами каталога ``data/reference``. Каждый
файл содержит указание на первоисточник и дату сверки, а загрузчик переносит
их в базу наравне с данными из внешних служб — с тем же журналом и теми же
проверками. Отличие только в способе получения, и оно зафиксировано
происхождением записи.

Полнота набора объявляется в самом файле и сообщается при загрузке. Частичный
охват — обычное состояние справочника, который ведётся вручную, и скрывать
его нельзя: доля охвата попадает в отчёт о качестве данных.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.choices import SourceType, UpdateFrequency
from core.models import DataSource, RoadSegment
from django.conf import settings
from django.db import transaction

from .osm.loaders import LoadReport

logger = logging.getLogger("freightflow.etl")

#: Код источника справочных наборов в реестре системы.
SOURCE_CODE = "reference"


class ReferenceError(RuntimeError):
    """Справочный набор отсутствует или составлен неверно."""


@dataclass(frozen=True)
class ReferenceFile:
    """Справочный набор вместе со сведениями о его происхождении."""

    payload: dict
    path: Path

    @property
    def source(self) -> dict:
        return self.payload.get("source") or {}

    @property
    def checked_at(self) -> date | None:
        raw = self.source.get("checked_at")
        try:
            return date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    @property
    def coverage(self) -> str:
        return self.source.get("coverage", "unknown")


def reference_dir() -> Path:
    return Path(settings.REFERENCE_DIR)


def read(name: str) -> ReferenceFile:
    """Прочитать справочный набор из каталога ``data/reference``."""
    path = reference_dir() / name
    if not path.exists():
        raise ReferenceError(
            f"Справочный набор {path} отсутствует. Наборы этого каталога "
            f"ведутся вручную и входят в поставку проекта."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReferenceError(f"Справочный набор {path} повреждён: {exc}") from exc
    return ReferenceFile(payload=payload, path=path)


def ensure_source() -> DataSource:
    """Справочная запись об источнике сведений, ведущихся вручную."""
    source, _ = DataSource.objects.update_or_create(
        code=SOURCE_CODE,
        defaults={
            "name": "Справочные наборы, ведущиеся вручную",
            "source_type": SourceType.MANUAL,
            "url": "",
            "update_frequency": UpdateFrequency.QUARTERLY,
            "is_active": True,
        },
    )
    return source


# ---------------------------------------------------------------------------
#  Грузовой каркас
# ---------------------------------------------------------------------------

FREIGHT_FRAME_FILE = "freight_frame.json"


def load_freight_frame() -> LoadReport:
    """Отметить в реестре магистрали, входящие в грузовой каркас.

    Магистрали сопоставляются по наименованию. Наименования в перечне
    и в реестре получены из разных источников, поэтому сопоставление
    ведётся без учёта регистра и написания «ё»: расхождение в одной букве
    не должно оставлять магистраль вне каркаса.

    Магистрали, не попавшие в перечень, помечаются как находящиеся вне
    каркаса только при полном охвате набора. При частичном охвате они
    остаются неопределёнными: отсутствие в неполном перечне не означает
    отсутствия в каркасе.
    """
    reference = read(FREIGHT_FRAME_FILE)
    report = LoadReport(dataset="Грузовой каркас")
    entries = reference.payload.get("roads") or []
    report.fetched = len(entries)

    registry = {_match_key(road.name): road for road in RoadSegment.objects.all()}

    with transaction.atomic():
        matched: set[int] = set()
        for entry in entries:
            name = (entry.get("name") or "").strip()
            road = registry.get(_match_key(name))
            if road is None:
                report.skipped += 1
                report.notes.append(f"магистраль «{name}» отсутствует в реестре")
                continue

            road.in_freight_frame = True
            road.freight_frame_kind = (entry.get("kind") or "")[:16]
            road.save(update_fields=["in_freight_frame", "freight_frame_kind"])
            matched.add(road.pk)
            report.updated += 1

        if reference.coverage == "full":
            outside = RoadSegment.objects.exclude(pk__in=matched)
            report.notes.append(
                f"вне каркаса отмечено магистралей: {outside.count()}"
            )
            outside.update(in_freight_frame=False, freight_frame_kind="")
        else:
            report.notes.append(
                "охват перечня частичный: магистрали вне перечня оставлены "
                "неопределёнными, а не отмечены как находящиеся вне каркаса"
            )

    total = RoadSegment.objects.count()
    if total:
        share = report.updated / total * 100
        report.notes.append(
            f"в каркас включено {report.updated} магистралей из {total} "
            f"({share:.0f} % реестра)"
        )
    return report


def _match_key(name: str) -> str:
    """Ключ сопоставления наименований из разных источников.

    Приводится регистр и написание «ё»: в перечне Департамента транспорта
    и в разметке OpenStreetMap оно расходится («Щёлковское» и «Щелковское»),
    а магистраль имеется в виду одна и та же.
    """
    return (name or "").strip().lower().replace("ё", "е")

"""Справочные наборы, которые не публикуются машиночитаемо.

Часть сведений предметной области существует только в виде схем и печатных
перечней: грузовой каркас Москвы Департамент транспорта публикует схемой,
ряды статистики Росстат — таблицами в изданиях. Выгрузки, пригодной для
автоматической загрузки, у них нет.

Такие сведения ведутся в проекте файлами каталога ``data/reference``. Каждый
файл содержит указание на первоисточник и дату сверки, а конвейер переносит
их в базу наравне с данными из внешних служб — с тем же журналом, теми же
проверками и тем же карантином. Отличие только в способе получения, и оно
зафиксировано происхождением записи.

Полнота набора объявляется в самом файле и сообщается при загрузке. Частичный
охват — обычное состояние справочника, который ведётся вручную, и скрывать
его нельзя: доля охвата попадает в отчёт о качестве данных.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.choices import SourceType, UpdateFrequency
from core.models import DataSource, RoadSegment
from django.conf import settings

from .pipeline import Candidate, Context, Extract, ModelPipeline, RunReport
from .quality import Check, condition

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


def match_key(name: str) -> str:
    """Ключ сопоставления наименований из разных источников.

    Приводится регистр и написание «ё»: в перечне Департамента транспорта
    и в разметке OpenStreetMap оно расходится («Щёлковское» и «Щелковское»),
    а магистраль имеется в виду одна и та же.
    """
    return (name or "").strip().lower().replace("ё", "е")


def _road_in_registry(candidate: Candidate) -> str | None:
    """Магистраль перечня должна присутствовать в реестре.

    Перечень ведётся вручную и по составу шире реестра: в него входят улицы,
    не относящиеся к магистральной сети. Несопоставленная запись — сведение
    о расхождении двух источников, и разбирать его должен человек.
    """
    if candidate.extra.get("road") is None:
        return (
            f"магистраль «{candidate.extra.get('name', '')}» отсутствует "
            f"в реестре улично-дорожной сети"
        )
    return None


class FreightFramePipeline(ModelPipeline):
    """Отметка магистралей, входящих в грузовой каркас Москвы.

    Магистрали сопоставляются по наименованию: наименования в перечне
    и в реестре получены из разных источников, поэтому сопоставление ведётся
    без учёта регистра и написания «ё» — расхождение в одной букве не должно
    оставлять магистраль вне каркаса.
    """

    name = "reference.frame"
    title = "Грузовой каркас"
    target_table = "road_segments"
    source_code = SOURCE_CODE
    description = (
        "Перечень магистралей, по которым допускается движение грузового "
        "транспорта тяжелее 2,5 т. Ведётся файлом data/reference/"
        f"{FREIGHT_FRAME_FILE} с указанием первоисточника и даты сверки."
    )
    model = RoadSegment
    frequency = UpdateFrequency.QUARTERLY
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        condition("reference.road", "Магистраль присутствует в реестре",
                  _road_in_registry),
    )

    def ensure_source(self) -> DataSource:
        return ensure_source()

    def lookup(self, candidate: Candidate) -> dict:
        return {"pk": candidate.extra["road"].pk}

    def extract(self, context: Context) -> Extract:
        reference = read(FREIGHT_FRAME_FILE)
        entries = reference.payload.get("roads") or []
        return Extract(
            records=entries,
            count=len(entries),
            fetched_at=None,
            notes=[],
        )

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        registry = {match_key(road.name): road for road in RoadSegment.objects.all()}
        matched: set[int] = report.state.setdefault("matched", set())

        for entry in extract.records:
            name = (entry.get("name") or "").strip()
            road = registry.get(match_key(name))
            if road is not None:
                matched.add(road.pk)
            yield Candidate(
                key=name,
                position=f"перечень, «{name}»",
                values={
                    "in_freight_frame": True,
                    "freight_frame_kind": (entry.get("kind") or "")[:16],
                },
                extra={"road": road, "name": name},
                payload=entry,
            )

    def verify(self, report: RunReport, context: Context) -> None:
        """Отметить положение магистралей вне перечня и сообщить охват.

        Магистрали, не попавшие в перечень, помечаются как находящиеся вне
        каркаса только при полном охвате набора. При частичном они остаются
        неопределёнными: отсутствие в неполном перечне не означает отсутствия
        в каркасе, и записать «вне каркаса» значило бы выдать пробел перечня
        за сведение о дороге.
        """
        reference = read(FREIGHT_FRAME_FILE)
        matched: set[int] = report.state.get("matched", set())

        if reference.coverage == "full":
            outside = RoadSegment.objects.exclude(pk__in=matched)
            report.detail(f"вне каркаса отмечено магистралей: {outside.count()}")
            if not context.dry_run:
                outside.update(in_freight_frame=False, freight_frame_kind="")
        else:
            report.detail(
                "охват перечня частичный: магистрали вне перечня оставлены "
                "неопределёнными, а не отмечены как находящиеся вне каркаса"
            )

        total = RoadSegment.objects.count()
        if total:
            share = len(matched) / total * 100
            report.detail(
                f"в каркас включено {len(matched)} магистралей из {total} "
                f"({share:.0f} % реестра)"
            )
        if reference.checked_at:
            report.detail(f"дата сверки перечня: {reference.checked_at:%d.%m.%Y}")


__all__ = [
    "FREIGHT_FRAME_FILE",
    "FreightFramePipeline",
    "ReferenceError",
    "ReferenceFile",
    "ensure_source",
    "match_key",
    "read",
    "reference_dir",
]

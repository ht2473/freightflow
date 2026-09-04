"""Подбор площадки под требования перевозчика.

Задача, ради которой ведётся реестр: перевозчику нужна площадка, на которую
доедет его транспортное средство и с которой удобно выходить на магистральную
сеть. Отбор здесь двухступенчатый — сначала требования, затем сопоставление.

**Требования** отсекают заведомо непригодное: площадь меньше нужной, чужой
округ, чужой тип объекта, слишком далеко от грузового каркаса. Требование
либо выполняется, либо нет, и торг между требованиями не ведётся.

**Сопоставление** упорядочивает то, что осталось. Оценка складывается из
величин, каждая из которых измерена или выведена из измеренного: площадь
объекта, удаление от грузового каркаса, удаление от федерального коридора,
разрешительная нагрузка в точке и нагрузка округа по индексу.

Оценка **относительна**: она нормируется по самой выборке и говорит, чем
площадка лучше других найденных, а не какова она сама по себе. Другой набор
требований даст другие баллы тем же объектам, и это свойство подхода,
а не его изъян — абсолютной шкалы пригодности площадки не существует.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from core.models import (
    CargoRoute,
    InfrastructureObject,
    RestrictionZone,
    RoadSegment,
)
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from geo.geometry import distance_to_polyline_km

from . import services

#: Шаг прореживания осей. Магистраль размечена вершинами через десятки
#: метров, тогда как расстояние до неё нужно с точностью до сотен: каждая
#: пятая вершина сохраняет форму линии и на порядок сокращает перебор.
VERTEX_STEP = 5

#: Наибольшее число площадок в итоге сопоставления.
SHORTLIST = 25


@dataclass(frozen=True)
class Criterion:
    """Составляющая оценки площадки.

    Атрибуты:
        code: обозначение составляющей;
        title: подпись;
        unit: единица измерения исходной величины;
        higher_is_better: направление шкалы;
        note: чем составляющая обоснована.
    """

    code: str
    title: str
    unit: str
    higher_is_better: bool
    note: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "area", _("Площадь площадки"), _("м²"), True,
        _("Измерена по контуру объекта в разметке источника"),
    ),
    Criterion(
        "frame", _("Удаление от грузового каркаса"), _("км"), False,
        _("Расстояние до ближайшей улицы, по которой разрешено движение "
          "грузового транспорта"),
    ),
    Criterion(
        "corridor", _("Удаление от федерального коридора"), _("км"), False,
        _("Расстояние до ближайшей трассы, связывающей город с сетью страны"),
    ),
    Criterion(
        "permits", _("Разрешительная нагрузка"), _("зон"), False,
        _("Число зон ограничения, в которые попадает точка: каждая требует "
          "своего пропуска для тяжёлого транспорта"),
    ),
    Criterion(
        "load", _("Нагрузка округа"), _("баллов"), False,
        _("Индекс логистической нагрузки округа размещения"),
    ),
)

CRITERION_BY_CODE: dict[str, Criterion] = {item.code: item for item in CRITERIA}

#: Веса по умолчанию. Равные: обосновать иное соотношение нечем, а скрытое
#: предпочтение одной составляющей исказило бы порядок незаметно для читателя.
DEFAULT_WEIGHTS: dict[str, float] = {item.code: 1.0 for item in CRITERIA}


@dataclass(frozen=True)
class Requirements:
    """Требования к площадке."""

    mass_tons: Decimal = Decimal("3.5")
    min_area_sq_m: float | None = None
    district_id: int | None = None
    type_id: int | None = None
    max_frame_km: float | None = None
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


@dataclass
class Candidate:
    """Площадка, прошедшая требования, с разбором оценки."""

    obj: InfrastructureObject
    values: dict[str, float | None]
    scores: dict[str, float] = field(default_factory=dict)
    zones: list[RestrictionZone] = field(default_factory=list)
    total: float = 0.0

    @property
    def permit_zone(self) -> RestrictionZone | None:
        """Зона, пропуск в которую потребуется заданной машине."""
        return self.zones[-1] if self.zones else None


def select(requirements: Requirements) -> dict:
    """Подобрать площадки под требования и упорядочить их сопоставлением."""
    zones = list(RestrictionZone.objects.exclude(geom__isnull=True).order_by("level"))
    frame_lines = _frame_lines()
    corridor_lines = _corridor_lines()
    load_by_district = _district_load()

    # Рассмотренными считаются все площадки реестра с координатами: без них
    # ни удаление от каркаса, ни разрешительную нагрузку определить нельзя.
    considered = InfrastructureObject.objects.located().count()

    queryset = (
        InfrastructureObject.objects.located()
        .with_refs()
        .in_district(requirements.district_id)
        .of_type(requirements.type_id)
    )
    if requirements.min_area_sq_m:
        queryset = queryset.filter(area_sq_m__gte=requirements.min_area_sq_m)

    candidates: list[Candidate] = []
    for obj in queryset:
        lon, lat = obj.geom.lon, obj.geom.lat
        frame_km = _nearest_km(frame_lines, lon, lat)
        if requirements.max_frame_km is not None and frame_km > requirements.max_frame_km:
            continue

        reached = [zone for zone in zones if zone.contains(lon, lat)]
        # Пропуск требуется в те зоны, где порог по массе машину задевает;
        # прочие зоны разрешительной нагрузки для неё не создают.
        demanding = [
            zone
            for zone in reached
            if requirements.mass_tons >= zone.permit_required_from_tons
        ]
        candidates.append(
            Candidate(
                obj=obj,
                zones=demanding,
                values={
                    "area": float(obj.area_sq_m) if obj.area_sq_m is not None else None,
                    "frame": frame_km if math.isfinite(frame_km) else None,
                    "corridor": _finite(_nearest_km(corridor_lines, lon, lat)),
                    "permits": float(len(demanding)),
                    "load": load_by_district.get(obj.district_id),
                },
            )
        )

    _rank(candidates, requirements.weights)
    return {
        "requirements": requirements,
        "considered": considered,
        "matched": len(candidates),
        "candidates": candidates[:SHORTLIST],
        "criteria": CRITERIA,
    }


def _rank(candidates: list[Candidate], weights: dict[str, float]) -> None:
    """Нормировать составляющие по выборке и упорядочить площадки.

    Составляющая, не измеренная ни у одной площадки, из оценки исключается
    с перераспределением веса: подставить вместо неё середину шкалы значило
    бы выдать отсутствие сведений за среднее значение.
    """
    if not candidates:
        return

    usable: dict[str, tuple[float, float]] = {}
    for criterion in CRITERIA:
        measured = [
            item.values[criterion.code]
            for item in candidates
            if item.values.get(criterion.code) is not None
        ]
        if measured:
            usable[criterion.code] = (min(measured), max(measured))

    total_weight = sum(weights.get(code, 0.0) for code in usable) or 1.0
    for item in candidates:
        item.scores = {}
        for code, (low, high) in usable.items():
            raw = item.values.get(code)
            if raw is None:
                continue
            span = high - low
            share = 0.5 if span == 0 else (raw - low) / span
            criterion = CRITERION_BY_CODE[code]
            item.scores[code] = share if criterion.higher_is_better else 1.0 - share
        item.total = round(
            sum(
                value * weights.get(code, 0.0)
                for code, value in item.scores.items()
            )
            / total_weight
            * 100,
            1,
        )

    candidates.sort(key=lambda item: (-item.total, item.obj.name))


def _nearest_km(lines: list[list], lon: float, lat: float) -> float:
    """Расстояние до ближайшей из линий набора."""
    if not lines:
        return math.inf
    return min(distance_to_polyline_km(line, lon, lat) for line in lines)


def _finite(value: float) -> float | None:
    """Заменить бесконечность отсутствием значения."""
    return value if math.isfinite(value) else None


def _frame_lines() -> list[list]:
    """Оси улиц грузового каркаса."""
    return _cached(
        "analytics:siting:frame",
        lambda: _thin(
            RoadSegment.objects.filter(in_freight_frame=True)
            .exclude(geom__isnull=True)
            .values_list("geom", flat=True)
        ),
    )


def _corridor_lines() -> list[list]:
    """Оси федеральных грузовых коридоров."""
    return _cached(
        "analytics:siting:corridors",
        lambda: _thin(
            CargoRoute.objects.exclude(geom__isnull=True).values_list("geom", flat=True)
        ),
    )


def _thin(geometries) -> list[list]:
    """Проредить оси линий, сохранив их форму.

    Линии остаются линиями: расстояние измеряется до самой оси, и потеря
    промежуточной вершины меняет его на доли её отклонения — тогда как
    замена линии облаком вершин отнесла бы площадку посреди длинного звена
    к дороге, до которой километры.
    """
    lines: list[list] = []
    for geometry in geometries:
        if geometry is None:
            continue
        vertices = geometry.points
        if len(vertices) < 2:
            continue
        thinned = vertices[::VERTEX_STEP]
        # Конец линии сохраняется всегда: прореживание не должно укорачивать
        # магистраль, обрывая её за несколько сотен метров до перекрёстка.
        if thinned[-1] != vertices[-1]:
            thinned.append(vertices[-1])
        lines.append(thinned)
    return lines


def _district_load() -> dict[int, float | None]:
    """Индекс логистической нагрузки по округам."""
    return {row["district"].id: row["score"] for row in services.load_index()}


def _cached(key: str, builder):
    """Кешировать оси на срок, заданный настройками."""
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, settings.ANALYTICS_CACHE_TTL)
    return value


def invalidate() -> None:
    """Сбросить кеш осей."""
    cache.delete_many(["analytics:siting:frame", "analytics:siting:corridors"])


__all__ = [
    "CRITERIA",
    "CRITERION_BY_CODE",
    "DEFAULT_WEIGHTS",
    "Candidate",
    "Requirements",
    "invalidate",
    "select",
]

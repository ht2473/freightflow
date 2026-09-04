"""Загрузка природных территорий — подложки карты.

Река, водохранилища и лесопарки задают узнаваемый рисунок города: без них
положение склада читается только по подписи округа. Подложка собирается из
той же разметки OpenStreetMap, что и остальной реестр, поэтому карта целиком
обслуживается системой и не зависит от сторонних тайловых служб.

Мелкие контуры отсеиваются по площади. Дворовый пруд и газон между домами
размечены наравне с Химкинским водохранилищем и Лосиным Островом, но
неразличимы на любом масштабе, где виден город, а по числу записей
превосходят всё остальное содержимое карты.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.choices import NaturalKind, OsmElement
from core.models import NaturalArea

from ..pipeline import Candidate, Context, Extract, RunReport
from ..quality import Check, not_negative, required
from . import queries
from .loaders import OverpassPipeline, ensure_source

#: Наименьшая площадь водной поверхности, попадающей на подложку, м².
#: Половина гектара — примерно пруд в парке: он ещё различим на масштабе
#: района, тогда как всё, что мельче, сливается с застройкой.
MIN_WATER_AREA_SQ_M = 5_000

#: Наименьшая площадь зелёного массива, м². Два гектара — сквер; меньшие
#: контуры на карте города не читаются.
MIN_GREEN_AREA_SQ_M = 20_000

#: Разметка, по которой территория относится к воде.
WATER_TAGS = (("natural", "water"), ("water", "river"), ("waterway", "riverbank"))

#: Разметка, по которой территория относится к зелёному массиву.
GREEN_TAGS = (("leisure", "park"), ("landuse", "forest"))


def classify_kind(tags: dict[str, str]) -> str | None:
    """Отнести элемент разметки к виду природной территории.

    Вода проверяется первой: пойменный парк размечен и как ``leisure=park``,
    и как участок русла, но препятствием для движения его делает именно
    вода, а не зелень.
    """
    if any(tags.get(key) == value for key, value in WATER_TAGS):
        return NaturalKind.WATER
    if any(tags.get(key) == value for key, value in GREEN_TAGS):
        return NaturalKind.GREEN
    return None


def minimum_area(kind: str) -> int:
    """Порог включения территории в подложку по её виду."""
    return MIN_WATER_AREA_SQ_M if kind == NaturalKind.WATER else MIN_GREEN_AREA_SQ_M


class NaturalAreasPipeline(OverpassPipeline):
    """Водные поверхности и зелёные массивы города."""

    name = "osm.natural"
    title = "Природные территории"
    target_table = "natural_areas"
    description = (
        "Водные поверхности и зелёные массивы для подложки карты. "
        "Контуры мельче порога различимости не загружаются: подложка "
        "показывает рисунок города, а не всю разметку."
    )
    query = queries.NATURAL_AREAS
    model = NaturalArea
    supports_prune = True
    volatile_fields = ()
    checks: tuple[Check, ...] = (
        required("kind", "Вид территории"),
        required("geom", "Контур территории"),
        not_negative("area_sq_m", "Площадь"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        osm_type, osm_id = candidate.extra["osm_key"]
        return {"osm_type": osm_type, "osm_id": osm_id}

    def prepare(
        self, extract: Extract, context: Context, report: RunReport
    ) -> Iterator[Candidate]:
        from .geometry import extract as extract_geometry

        source = ensure_source()

        for element in extract.records:
            tags = element.get("tags") or {}
            osm_type, osm_id = element.get("type"), element.get("id")
            if osm_type not in OsmElement.values or osm_id is None or not tags:
                report.skip("элемент без разметки")
                continue

            kind = classify_kind(tags)
            if kind is None:
                report.skip("разметка не относится к природной территории")
                continue

            geometry = extract_geometry(element)
            if geometry.footprint is None or geometry.area_sq_m is None:
                # Русло, размеченное линией без замыкания, площади не имеет:
                # закрашивать по нему нечего.
                report.skip("контур не размечен")
                continue

            if geometry.area_sq_m < minimum_area(kind):
                report.skip("мельче порога различимости")
                continue

            yield Candidate(
                key=f"{osm_type}/{osm_id}",
                position=f"{osm_type}/{osm_id}",
                values={
                    "kind": kind,
                    "name": (tags.get("name") or "")[:200],
                    "geom": geometry.footprint,
                    "area_sq_m": round(geometry.area_sq_m, 2),
                    "source": source,
                },
                extra={"osm_key": (osm_type, int(osm_id))},
                payload=tags,
            )

    def prune(self, seen: set[str], context: Context) -> int:
        doomed = [
            pk
            for pk, osm_type, osm_id in NaturalArea.objects.values_list(
                "pk", "osm_type", "osm_id"
            ).iterator()
            if f"{osm_type}/{osm_id}" not in seen
        ]
        if not doomed:
            return 0
        removed, _ = NaturalArea.objects.filter(pk__in=doomed).delete()
        return removed


__all__ = ["MIN_GREEN_AREA_SQ_M", "MIN_WATER_AREA_SQ_M", "NaturalAreasPipeline", "classify_kind"]

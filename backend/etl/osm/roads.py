"""Загрузка магистральной улично-дорожной сети.

Дорога в OpenStreetMap разбита на части: новая часть начинается на каждом
перекрёстке и при любой смене характеристик, а разделённая проезжая часть
размечена двумя независимыми линиями. Варшавское шоссе представлено
тремястами двадцатью пятью такими частями.

Реестру такое дробление не нужно: пользователь ищет магистраль, а не отрезок
между светофорами. Загрузчик собирает части в одну запись по наименованию,
сохраняя геометрию набором линий и агрегируя характеристики.

Протяжённость записывается как суммарная длина проезжих частей. Величина
измеряемая и однозначная, в отличие от протяжённости трассы: свести две
односторонние проезжие части к одной осевой линии можно лишь допущением,
которое на части магистралей неверно — проверка по МКАД, Каширскому и
Ленинградскому шоссе дала расхождения в обе стороны.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterator

from core.choices import DataOrigin, RoadClass
from core.models import District, RoadSegment
from geo.geometry import Geometry

from ..pipeline import Candidate, Context, Extract, RunReport
from ..quality import Check, fits, required, within
from . import queries
from .loaders import OverpassPipeline

logger = logging.getLogger("freightflow.etl")

#: Соответствие классов OpenStreetMap классификации улично-дорожной сети.
#:
#: ``motorway`` — дороги с полностью изолированным движением: МКАД, ЦКАД,
#: скоростные диаметры. ``trunk`` — магистрали с преимущественно непрерывным
#: движением: вылетные шоссе, хорды. ``primary`` — магистрали общегородского
#: значения с регулируемыми пересечениями: проспекты и улицы центра.
ROAD_CLASSES: dict[str, str] = {
    "motorway": RoadClass.HIGHWAY,
    "trunk": RoadClass.ARTERIAL,
    "primary": RoadClass.COLLECTOR,
}

#: Порядок значимости классов: у магистрали, собранной из частей разных
#: классов, берётся наиболее значимый.
CLASS_RANK: dict[str, int] = {
    RoadClass.HIGHWAY: 3,
    RoadClass.ARTERIAL: 2,
    RoadClass.COLLECTOR: 1,
}

#: Наименьшая протяжённость магистрали, при которой она попадает в реестр, км.
#: Более короткие записи — как правило, съезды и развязочные связки, у которых
#: наименование совпадает с основной магистралью.
MIN_ROAD_LENGTH_KM = 0.5

#: Ограничение скорости разбирается из значения вида «60» либо «60 mph».
_SPEED_RE = re.compile(r"^\s*(\d{1,3})")

#: Покилометровая разметка кольцевых магистралей: МКАД размечен как
#: «МКАД, 68-й километр», и без приведения наименования магистраль
#: распалась бы в реестре на сто девять записей по километру каждая.
_KILOMETRE_MARK_RE = re.compile(r"^(?P<road>.+?),\s*\d+-й\s+километр\s*$", re.IGNORECASE)

#: Дублирующие направления: «Ленинградское шоссе (внутренняя сторона)».
_SIDE_MARK_RE = re.compile(
    r"^(?P<road>.+?)\s*\((?:внутренняя|внешняя)\s+сторона\)\s*$", re.IGNORECASE
)


def normalize_road_name(name: str) -> str:
    """Привести наименование части к наименованию магистрали.

    Разметка сообщества именует части кольцевых магистралей по километру и
    по стороне кольца. Такие наименования обозначают положение на трассе,
    а не отдельную дорогу, и группировка по ним раздробила бы реестр.
    """
    cleaned = (name or "").strip()
    for pattern in (_KILOMETRE_MARK_RE, _SIDE_MARK_RE):
        match = pattern.match(cleaned)
        if match:
            cleaned = match.group("road").strip()
    return cleaned


def _int_tag(tags: dict[str, str], key: str) -> int | None:
    """Прочитать целочисленный тег, отбросив нечисловые значения.

    В разметке встречаются значения вида ``2;3`` (разное число полос по
    направлениям) и ``RU:urban`` вместо скорости. Такие значения
    отбрасываются: подставлять вместо них догадку нельзя.
    """
    raw = tags.get(key)
    if not raw:
        return None
    match = _SPEED_RE.match(str(raw))
    return int(match.group(1)) if match else None


def _hgv_allowed(tags_list: list[dict[str, str]]) -> bool | None:
    """Определить, открыта ли магистраль для грузового движения.

    Признак выводится только из явной разметки. Её мало — около полутора
    процентов участков, — поэтому у большинства магистралей значение
    остаётся неопределённым. Заполнять его умолчанием нельзя: ограничения
    движения грузового транспорта в Москве задаются не разметкой
    OpenStreetMap, а нормативным актом, и подмена одного другим создала бы
    ложную уверенность.
    """
    values = {tags.get("hgv") for tags in tags_list if tags.get("hgv")}
    if not values:
        return None
    if values & {"no", "destination"}:
        return False
    if values & {"yes", "designated"}:
        return True
    return None


def _road_key(tags: dict[str, str]) -> str | None:
    """Ключ группировки: наименование магистрали либо её учётный номер.

    Ключ приводится к нижнему регистру: разметка сообщества непоследовательна
    в написании прописных букв, и «Северо-Восточная хорда» соседствует
    с «Северо-восточной хордой». Без приведения одна магистраль попадала бы
    в реестр двумя записями. Написание для отображения выбирается отдельно —
    :func:`_display_name`.
    """
    name = normalize_road_name(tags.get("name") or "")
    if name:
        return name.lower()
    ref = (tags.get("ref") or "").strip()
    return ref.lower() or None


def _display_name(tags_list: list[dict[str, str]]) -> str:
    """Написание наименования для реестра — наиболее частое среди частей.

    Голосование по частям надёжнее любого правила расстановки прописных букв:
    в наименованиях встречаются и «шоссе Энтузиастов» со строчной, и
    «Проспект Мира» с прописной, и оба написания правильны.
    """
    spellings = Counter(
        normalize_road_name(tags.get("name") or "")
        for tags in tags_list
        if tags.get("name")
    )
    if spellings:
        return spellings.most_common(1)[0][0]
    refs = Counter((tags.get("ref") or "").strip() for tags in tags_list if tags.get("ref"))
    return refs.most_common(1)[0][0] if refs else ""


class RoadNetworkPipeline(OverpassPipeline):
    """Реестр магистралей улично-дорожной сети."""

    name = "osm.roads"
    title = "Магистральная сеть"
    target_table = "road_segments"
    description = (
        "Линии OpenStreetMap классов motorway, trunk и primary. Части "
        "собираются в одну магистраль по наименованию, характеристики "
        "агрегируются, протяжённость измеряется по геометрии."
    )
    query = queries.ROAD_NETWORK
    model = RoadSegment
    supports_prune = True
    checks: tuple[Check, ...] = (
        required("name", "Наименование магистрали"),
        fits("name", 200, "Наименование магистрали"),
        required("geom", "Геометрия магистрали"),
        within("length_km", 0.5, 500, "Протяжённость", "км"),
        within("lanes", 1, 16, "Число полос"),
        within("speed_limit_kmh", 5, 130, "Разрешённая скорость", "км/ч"),
    )

    def lookup(self, candidate: Candidate) -> dict:
        return {"name": candidate.key}

    def prepare(self, extract: Extract, context: Context,
                report: RunReport) -> Iterator[Candidate]:
        source = self.ensure_source()
        districts = _district_index()
        groups = _group_ways(extract.records, report)

        for key, ways in groups.items():
            values = _build_road(key, ways, districts, source, extract.fetched_at)
            if values is None:
                # Слишком короткие связки и части без геометрии магистралью
                # не являются: их наименование совпадает с основной дорогой.
                report.skip("связка короче порога включения")
                continue

            display_name = values.pop("name")
            yield Candidate(
                key=display_name,
                position=f"{len(ways)} частей, ключ «{key}»",
                values=values,
                extra={"name": display_name},
                payload={"key": key, "parts": len(ways)},
            )

    def prune(self, seen: set[str], context: Context) -> int:
        """Удалить магистрали, отсутствующие в текущей выгрузке."""
        doomed = [
            pk
            for pk, name in RoadSegment.objects.values_list("pk", "name").iterator(
                chunk_size=2000
            )
            if name not in seen
        ]
        if not doomed:
            return 0
        removed, _ = RoadSegment.objects.filter(pk__in=doomed).delete()
        return removed


def _group_ways(elements: list[dict], report: RunReport) -> dict[str, list[dict]]:
    """Сгруппировать части по наименованию магистрали."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for element in elements:
        tags = element.get("tags") or {}
        key = _road_key(tags)
        if key is None:
            # Безымянные части — съезды и развязочные связки. Самостоятельной
            # магистралью они не являются и в реестр не попадают.
            report.skip("часть без наименования")
            continue
        groups[key].append(element)
    return groups


def _district_index() -> list[tuple[District, Geometry]]:
    return [
        (district, district.geom)
        for district in District.objects.with_geometry().exclude(geom__isnull=True)
    ]


def _build_road(key: str, ways: list[dict], districts, source,
                fetched_at) -> dict | None:
    """Собрать запись магистрали из её частей."""
    parts: list[list[list[float]]] = []
    tags_list: list[dict[str, str]] = []

    for way in ways:
        coords = [
            [float(node["lon"]), float(node["lat"])]
            for node in (way.get("geometry") or [])
            if "lon" in node and "lat" in node
        ]
        if len(coords) >= 2:
            parts.append(coords)
            tags_list.append(way.get("tags") or {})

    if not parts:
        return None

    geometry = Geometry("MULTILINESTRING", parts)
    length_km = geometry.length_km
    if length_km < MIN_ROAD_LENGTH_KM:
        return None

    classes = [
        ROAD_CLASSES[tags["highway"]]
        for tags in tags_list
        if tags.get("highway") in ROAD_CLASSES
    ]
    road_class = max(classes, key=lambda code: CLASS_RANK[code]) if classes else (
        RoadClass.COLLECTOR
    )

    lanes = [value for value in (_int_tag(t, "lanes") for t in tags_list) if value]
    speeds = [value for value in (_int_tag(t, "maxspeed") for t in tags_list) if value]
    refs = [(t.get("ref") or "").strip() for t in tags_list if t.get("ref")]

    return {
        "name": (_display_name(tags_list) or key)[:200],
        "ref": refs[0][:32] if refs else "",
        "road_class": road_class,
        # Число полос — наибольшее из размеченных: оно определяет пропускную
        # способность магистрали, тогда как среднее сгладило бы её сужениями
        # на подходах к перекрёсткам.
        "lanes": min(max(lanes), 16) if lanes else None,
        "speed_limit_kmh": max(speeds) if speeds else None,
        "length_km": round(length_km, 2),
        "length_origin": DataOrigin.MEASURED,
        "allows_hgv": _hgv_allowed(tags_list),
        "segment_count": len(parts),
        "geom": geometry,
        "district": _dominant_district(geometry, districts),
        "source": source,
        "source_updated_at": fetched_at,
    }


def _dominant_district(geometry: Geometry, districts) -> District | None:
    """Округ, в котором лежит большая часть вершин магистрали.

    Магистрали пересекают несколько округов, поэтому единственный округ —
    это упрощение. Оно осознанное: признак служит для отбора в реестре
    («магистрали Южного округа»), а не для территориального учёта.
    """
    if not districts:
        return None

    # Голосование ведётся по разреженной выборке вершин: магистраль содержит
    # тысячи точек, а для определения преобладающего округа хватает сотни.
    points = geometry.points
    step = max(1, len(points) // 100)

    tally: dict[int, int] = defaultdict(int)
    index = {district.id: district for district, _ in districts}
    for lon, lat in points[::step]:
        for district, boundary in districts:
            if boundary.contains(lon, lat):
                tally[district.id] += 1
                break

    if not tally:
        return None
    return index[max(tally, key=lambda key: tally[key])]


__all__ = ["RoadNetworkPipeline", "normalize_road_name"]

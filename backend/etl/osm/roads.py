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

from core.choices import DataOrigin, RoadClass
from core.models import DataSource, District, RoadSegment
from django.db import transaction
from geo.geometry import Geometry

from ..client import OverpassClient
from . import queries
from .loaders import LoadReport, ensure_source

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


def load_road_network(client: OverpassClient, refresh: bool = False,
                      prune: bool = False) -> LoadReport:
    """Заполнить реестр магистралей улично-дорожной сети."""
    report = LoadReport(dataset="Магистральная сеть")
    response = client.fetch(queries.ROAD_NETWORK, refresh=refresh)
    report.fetched = response.count
    report.from_cache = response.from_cache
    report.fetched_at = response.fetched_at

    source = ensure_source()
    groups = _group_ways(response.elements, report)
    districts = _district_index()

    present: set[str] = set()
    with transaction.atomic():
        for key, ways in groups.items():
            record = _build_road(key, ways, districts, source, response.fetched_at)
            if record is None:
                report.skipped += 1
                continue

            display_name = record.pop("name")
            _, created = RoadSegment.objects.update_or_create(
                name=display_name, defaults=record,
            )
            # В набор сохранившихся заносится отображаемое наименование:
            # именно по нему приводится реестр к составу источника, тогда как
            # ключ группировки приведён к нижнему регистру.
            present.add(display_name)
            if created:
                report.created += 1
            else:
                report.updated += 1

        if prune:
            report.removed = _prune_roads(present)

    return report


def _group_ways(elements: list[dict], report: LoadReport) -> dict[str, list[dict]]:
    """Сгруппировать части по наименованию магистрали."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for element in elements:
        tags = element.get("tags") or {}
        key = _road_key(tags)
        if key is None:
            # Безымянные части — съезды и развязочные связки. Самостоятельной
            # магистралью они не являются и в реестр не попадают.
            report.skipped += 1
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

    display_name = _display_name(tags_list) or key
    return {
        "name": display_name[:200],
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


def _prune_roads(present: set[str]) -> int:
    """Удалить магистрали, отсутствующие в текущей выгрузке."""
    doomed = [
        pk
        for pk, name in RoadSegment.objects.values_list("pk", "name").iterator(
            chunk_size=2000
        )
        if name not in present
    ]
    if not doomed:
        return 0
    removed, _ = RoadSegment.objects.filter(pk__in=doomed).delete()
    return removed


__all__ = ["load_road_network", "DataSource"]

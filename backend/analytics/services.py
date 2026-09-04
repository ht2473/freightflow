"""Расчётное ядро аналитического модуля.

Модуль реализует четыре группы методов:

1. **Композитный индекс логистической нагрузки** — сведение разнородных
   показателей округа к единой сопоставимой оценке;
2. **Типология округов** — разбиение на однородные группы методом k-средних;
3. **Прогнозирование грузопотока** — модель тренда с сезонной составляющей и
   оценкой качества аппроксимации;
4. **Сценарное моделирование** — пересчёт нагрузки при заданных изменениях.

Все алгоритмы реализованы средствами стандартной библиотеки. Решение принято
осознанно: объём выборки (двенадцать округов, десятки месяцев наблюдений) не
требует специализированных библиотек, а отсутствие тяжёлых зависимостей
упрощает развёртывание и делает расчёты полностью прозрачными для проверки.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date

from core import selectors
from core.choices import DataOrigin, FlowScope, PeriodType
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Component:
    """Составляющая композитного индекса.

    Описание составляющей ведётся вместе с её весом: и то и другое нужно
    и расчёту, и пояснению к показателю на странице, и разделу методики.
    Разнесённые по разным местам, они разошлись бы при первой же правке.
    """

    key: str
    title: str
    unit: str
    formula: str
    meaning: str
    weight: float
    origin: str
    source: str
    #: Обратная составляющая: рост величины означает снижение нагрузки.
    inverse: bool = False

    @property
    def origin_label(self) -> str:
        """Подпись происхождения величины для маркера в интерфейсе."""
        return DataOrigin(self.origin).label


#: Состав композитного индекса логистической нагрузки округа.
#:
#: В индекс входят только измеренные величины. Расчётная оценка загруженности
#: сети в него не включена: модельное значение, взвешенное наравне
#: с измеренными, определяло бы четверть итога и выдавало бы допущение модели
#: за свойство округа. Загруженность приводится рядом отдельным показателем
#: со своим маркером происхождения.
#:
#: Все составляющие удельные. Абсолютные величины сравнивали бы не нагрузку,
#: а размер: Троицкий округ в шестнадцать раз больше Центрального, и любой
#: его валовой показатель оказался бы наибольшим.
COMPONENTS: tuple[Component, ...] = (
    Component(
        key="storage",
        title=_("Концентрация складских площадей"),
        unit=_("м² на км² территории"),
        formula="Σ площадь контуров объектов ÷ площадь округа",
        meaning=_(
            "Складские мощности порождают грузовое движение: каждый объект — "
            "это подъезд, разгрузка и обратный рейс."
        ),
        weight=0.35,
        origin=DataOrigin.MEASURED,
        source=_("OpenStreetMap, контуры объектов реестра"),
    ),
    Component(
        key="network",
        title=_("Обеспеченность магистральной сетью"),
        unit=_("км магистралей на км² территории"),
        formula="Σ протяжённость магистралей ÷ площадь округа",
        meaning=_(
            "Густая магистральная сеть распределяет тот же поток по большему "
            "числу направлений, поэтому составляющая входит в индекс обратно."
        ),
        weight=0.25,
        origin=DataOrigin.MEASURED,
        source=_("OpenStreetMap, реестр магистралей"),
        inverse=True,
    ),
    Component(
        key="restrictions",
        title=_("Помехи движению"),
        unit=_("работ на 100 км магистралей"),
        formula="работы, затрагивающие грузовое движение × 100 ÷ протяжённость сети",
        meaning=_(
            "Участок, закрытый на реконструкцию, перекладывает поток "
            "на соседние направления и сокращает пропускную способность."
        ),
        weight=0.20,
        origin=DataOrigin.MEASURED,
        source=_("OpenStreetMap, отметка highway=construction"),
    ),
    Component(
        key="residential",
        title=_("Плотность жилой застройки"),
        unit=_("человек на км²"),
        formula="численность населения ÷ площадь округа",
        meaning=_(
            "Один и тот же поток в плотно населённом округе проходит ближе "
            "к жилью, и ограничения движения там строже."
        ),
        weight=0.20,
        origin=DataOrigin.MEASURED,
        source=_("справочник округов"),
    ),
)

#: Составляющая по её обозначению: обращений по ключу больше, чем перебора.
COMPONENT_BY_KEY: dict[str, Component] = {item.key: item for item in COMPONENTS}

#: Экспертные веса составляющих. Сумма равна единице.
#:
#: Порядок величин задан соотношением «спрос важнее условий»: складские
#: мощности порождают движение, тогда как сеть, помехи и застройка определяют
#: лишь то, каким оно окажется. Числа внутри этого порядка выбраны экспертно,
#: и устойчивость ранжирования к их выбору измеряется анализом
#: чувствительности, а не постулируется.
INDEX_WEIGHTS: dict[str, float] = {item.key: item.weight for item in COMPONENTS}

#: Подписи составляющих для легенд и таблиц.
INDEX_COMPONENTS: dict[str, str] = {item.key: item.title for item in COMPONENTS}

#: Чем отличается группа, у которой выделяется данная составляющая.
#:
#: Названия групп выводятся из их состава, а не назначаются списком:
#: число групп задаёт пользователь, и заготовленная подпись при другом
#: их числе описывала бы уже не то разбиение.
CLUSTER_TRAITS: dict[str, str] = {
    "storage": _("Округа концентрации складских площадей"),
    "network": _("Округа со слабой магистральной сетью"),
    "restrictions": _("Округа с наибольшими помехами движению"),
    "residential": _("Округа плотной жилой застройки"),
}

#: Отклонение центра группы, начиная с которого признак считается выраженным.
#: Признаки стандартизованы, поэтому величина измеряется в долях отклонения.
TRAIT_THRESHOLD = 0.3


# ---------------------------------------------------------------------------
#  Нормирование
# ---------------------------------------------------------------------------


def min_max_normalize(values: list[float]) -> list[float]:
    """Привести ряд к отрезку [0; 1] линейным преобразованием.

    Если разброс вырожден (все значения совпадают), возвращается ряд из
    значений 0,5 — это исключает деление на ноль и не искажает сопоставление.
    """
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [0.5] * len(values)
    span = high - low
    return [(value - low) / span for value in values]


def z_scores(values: list[float]) -> list[float]:
    """Стандартизовать ряд: нулевое среднее, единичное отклонение."""
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = math.sqrt(variance)
    if math.isclose(deviation, 0.0):
        return [0.0] * len(values)
    return [(value - mean) / deviation for value in values]


def _normalize_column(values: list[float | None], inverse: bool) -> list[float | None]:
    """Нормировать составляющую с учётом её направления.

    Пропуск остаётся пропуском: подставить вместо него ноль значило бы
    объявить величину измеренной и наименьшей в выборке.
    """
    present = [value for value in values if value is not None]
    if not present:
        return [None] * len(values)
    scaled = dict(zip(present, min_max_normalize(present), strict=False))
    return [
        None if value is None else (1 - scaled[value] if inverse else scaled[value])
        for value in values
    ]


# ---------------------------------------------------------------------------
#  1. Композитный индекс логистической нагрузки
# ---------------------------------------------------------------------------


def district_metrics() -> list[dict]:
    """Исходные величины составляющих индекса по каждому округу.

    Величины удельные: делятся на площадь округа либо на протяжённость его
    магистральной сети. Знаменатель, равный нулю или неизвестный, оставляет
    величину неопределённой — частного в этом случае не существует.
    """
    profiles = selectors.district_profiles()
    if not profiles:
        return []

    incidents = _cargo_incidents_by_district()

    rows: list[dict] = []
    for profile in profiles:
        district = profile["district"]
        area = float(district.area_sq_km) if district.area_sq_km else None
        network = profile["road_length_km"] or None
        events = incidents.get(district.id, 0)

        rows.append(
            {
                "district": district,
                "values": {
                    "storage": (
                        profile["area_sq_m"] / area
                        if area and profile["area_sq_m"]
                        else None
                    ),
                    "network": network / area if area and network else None,
                    "restrictions": events * 100 / network if network else None,
                    "residential": (
                        district.population / area
                        if area and district.population
                        else None
                    ),
                },
                "object_count": profile["object_count"],
                "road_length_km": profile["road_length_km"],
                "incidents": events,
                "congestion": profile["congestion"],
            }
        )
    return rows


def _cargo_incidents_by_district() -> dict[int, int]:
    """Число событий, затрагивающих грузовое движение, в разрезе округов.

    Учитываются только такие события: закрытие переулка условий грузовой
    перевозки не меняет, а в общем счёте выглядело бы наравне с закрытием
    вылетной магистрали.
    """
    from core.models import TrafficIncident
    from django.db.models import Count

    rows = (
        TrafficIncident.objects.filter(district__isnull=False, affects_cargo=True)
        .values("district_id")
        .annotate(count=Count("id"))
    )
    return {row["district_id"]: row["count"] for row in rows}


def _score_row(shares: dict[str, float | None], weights: dict[str, float]) -> float:
    """Свернуть нормированные составляющие округа в оценку.

    Неопределённая составляющая из свёртки исключается, а её вес
    распределяется между остальными: иначе пропуск в источнике вёл бы себя
    как измеренный ноль и занижал бы оценку округа.
    """
    present = {
        key: weight for key, weight in weights.items() if shares.get(key) is not None
    }
    total = sum(present.values())
    if not total:
        return 0.0
    return sum(shares[key] * weight for key, weight in present.items()) / total


def load_index(weights: dict[str, float] | None = None) -> list[dict]:
    """Рассчитать индекс логистической нагрузки по всем округам.

    Порядок расчёта:

    1. по каждому округу собираются удельные величины составляющих;
    2. каждая нормируется методом «минимум — максимум», что делает
       разноразмерные величины (м²/км², км/км², чел./км²) сопоставимыми;
       обратная составляющая при этом обращается;
    3. нормированные значения взвешиваются и суммируются;
    4. итог переводится в стобалльную шкалу и ранжируется.

    Набор весов можно задать явно — этим пользуется анализ чувствительности.
    Возвращает список словарей, упорядоченный по убыванию индекса.
    """
    scheme = weights or INDEX_WEIGHTS

    def build() -> list[dict]:
        metrics = district_metrics()
        if not metrics:
            return []

        normalized = {
            item.key: _normalize_column(
                [row["values"][item.key] for row in metrics], item.inverse
            )
            for item in COMPONENTS
        }

        rows: list[dict] = []
        for position, metric in enumerate(metrics):
            shares = {item.key: normalized[item.key][position] for item in COMPONENTS}
            rows.append(
                {
                    "district": metric["district"],
                    "score": round(_score_row(shares, scheme) * 100, 1),
                    "shares": shares,
                    "components": {
                        key: None if value is None else round(value * 100, 1)
                        for key, value in shares.items()
                    },
                    "weights": scheme,
                    "raw": dict(metric["values"]),
                    # Отличие неизмеренной величины от измеренного нуля
                    # сохраняется отдельно: в расчёте они ведут себя
                    # одинаково, а в карточке округа — нет.
                    "measured": {
                        key: value is not None for key, value in metric["values"].items()
                    },
                    "object_count": metric["object_count"],
                    "road_length_km": metric["road_length_km"],
                    "incidents": metric["incidents"],
                    "congestion": metric["congestion"],
                }
            )

        rows.sort(key=lambda row: row["score"], reverse=True)
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    # Кешируется только штатный набор весов: наборы анализа чувствительности
    # перебираются десятками и вытеснили бы из кеша всё остальное.
    return _cached("analytics:load_index", build) if weights is None else build()


def index_formula() -> str:
    """Формула индекса словами — для раздела методики.

    Собирается из реестра составляющих: выписанная в разметке отдельно,
    она разошлась бы с расчётом при первом же пересмотре весов.
    """
    parts = [
        "{:.2f} · {}{}".format(
            item.weight, str(item.title).lower(), " (обратно)" if item.inverse else ""
        )
        for item in COMPONENTS
    ]
    return "Индекс = " + " + ".join(parts).replace(".", ",")


def index_summary() -> dict:
    """Обобщающие характеристики распределения индекса."""
    rows = load_index()
    if not rows:
        return {"count": 0}
    scores = [row["score"] for row in rows]
    mean = sum(scores) / len(scores)
    spread = max(scores) - min(scores)
    return {
        "count": len(rows),
        "mean": round(mean, 1),
        "max": rows[0],
        "min": rows[-1],
        "spread": round(spread, 1),
        # Коэффициент вариации показывает степень неоднородности округов:
        # значение выше 30 % свидетельствует о выраженной поляризации.
        "variation": round(
            (math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores)) / mean * 100), 1
        )
        if mean
        else 0.0,
    }


# ---------------------------------------------------------------------------
#  1a. Обоснование весов и анализ чувствительности
# ---------------------------------------------------------------------------


def equal_weights() -> dict[str, float]:
    """Равные веса: положение, при котором ни одна составляющая не выделена."""
    share = 1 / len(COMPONENTS)
    return {item.key: share for item in COMPONENTS}


def entropy_weights(rows: list[dict] | None = None) -> dict[str, float]:
    """Веса по мере различительной способности составляющей.

    Метод энтропии Шеннона: составляющая, значения которой у всех округов
    близки, ничего о них не сообщает и получает малый вес; составляющая,
    разводящая округа далеко, получает больший. Веса выводятся из самих
    данных и не зависят от мнения — этим они и полезны рядом с экспертными:
    совпадение порядка величин подтверждает экспертную оценку, расхождение
    указывает, где она держится на одном соглашении.

    Величины берутся нормированными, поэтому единицы измерения на результат
    не влияют. Нулевая доля в сумму энтропии не входит: предел p·ln p при
    p → 0 равен нулю.
    """
    rows = rows if rows is not None else load_index()
    if len(rows) < 2:
        return dict(INDEX_WEIGHTS)

    diversity: dict[str, float] = {}
    scale = 1 / math.log(len(rows))
    for item in COMPONENTS:
        values = [_share(row, item.key) for row in rows]
        total = sum(values)
        if not total:
            diversity[item.key] = 0.0
            continue
        shares = [value / total for value in values]
        entropy = -scale * sum(p * math.log(p) for p in shares if p > 0)
        diversity[item.key] = max(1 - entropy, 0.0)

    spread = sum(diversity.values())
    if not spread:
        return dict(INDEX_WEIGHTS)
    return {key: value / spread for key, value in diversity.items()}


def spearman(first: list[int], second: list[int]) -> float:
    """Коэффициент ранговой корреляции Спирмена для двух ранжирований.

    Отвечает на вопрос, ради которого и ведётся анализ чувствительности:
    насколько порядок округов сохраняется при другом наборе весов. Связок
    в ранжировании нет — места нумеруются подряд, — поэтому применима
    формула через сумму квадратов разностей.
    """
    count = len(first)
    if count < 2:
        return 1.0
    squares = sum((a - b) ** 2 for a, b in zip(first, second, strict=False))
    return 1 - 6 * squares / (count * (count**2 - 1))


def pearson(first: list[float], second: list[float]) -> float:
    """Коэффициент линейной корреляции двух рядов."""
    count = len(first)
    if count < 2:
        return 0.0
    mean_a = sum(first) / count
    mean_b = sum(second) / count
    covariance = sum(
        (a - mean_a) * (b - mean_b) for a, b in zip(first, second, strict=False)
    )
    spread_a = math.sqrt(sum((a - mean_a) ** 2 for a in first))
    spread_b = math.sqrt(sum((b - mean_b) ** 2 for b in second))
    if not spread_a or not spread_b:
        return 0.0
    return covariance / (spread_a * spread_b)


#: Величина отклонения веса в одностороннем испытании.
#:
#: Четверть — не круглое число ради круглого: меньший сдвиг ничего не выявляет
#: на выборке в двенадцать округов, больший превращает испытание в другой
#: набор весов, а не в проверку устойчивости имеющегося.
WEIGHT_PERTURBATION = 0.25


def _ranking(rows: list[dict]) -> dict[int, int]:
    """Место каждого округа в ранжировании."""
    return {row["district"].id: row["rank"] for row in rows}


def _compare_ranking(base: dict[int, int], other: list[dict]) -> dict:
    """Сопоставить ранжирование с базовым."""
    ranking = _ranking(other)
    order = sorted(base)
    shifts = {key: abs(base[key] - ranking[key]) for key in order}
    return {
        "rows": other,
        "leader": other[0]["district"].short_name,
        "correlation": round(
            spearman([base[key] for key in order], [ranking[key] for key in order]), 3
        ),
        "max_shift": max(shifts.values()) if shifts else 0,
        "unchanged": sum(1 for value in shifts.values() if value == 0),
        "moved": sorted(
            (
                {"district": row["district"], "shift": base[row["district"].id] - row["rank"]}
                for row in other
                if base[row["district"].id] != row["rank"]
            ),
            key=lambda item: abs(item["shift"]),
            reverse=True,
        ),
    }


def _normalized_weights(weights: dict[str, float]) -> dict[str, float]:
    """Привести набор весов к единичной сумме."""
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()} if total else weights


def sensitivity() -> dict:
    """Проверить устойчивость ранжирования к выбору весов.

    Веса композитного показателя назначаются, а не измеряются, и потому
    требуют не защиты, а проверки: показать надо не то, что они верны,
    а то, насколько от них зависит вывод. Проверка ведётся тремя способами.

    1. **Другие наборы весов** — равные и выведенные из данных методом
       энтропии. Если порядок округов при них сохраняется, вывод опирается
       на данные, а не на выбор весов.
    2. **Одностороннее отклонение** — вес каждой составляющей поочерёдно
       изменяется на четверть, остальные пропорционально пересчитываются.
       Так видно, какая составляющая определяет расстановку.
    3. **Взаимная корреляция составляющих** — сильно связанные величины
       учитывают одно и то же дважды, и суммарный вес такой пары выше
       объявленного.
    """

    def build() -> dict:
        base_rows = load_index()
        if len(base_rows) < 2:
            return {"available": False}

        base = _ranking(base_rows)
        schemes = [
            {
                "code": "expert",
                "title": _("Экспертный"),
                "note": _("действующий набор: спрос важнее условий"),
                "weights": dict(INDEX_WEIGHTS),
            },
            {
                "code": "equal",
                "title": _("Равные веса"),
                "note": _("ни одна составляющая не выделена"),
                "weights": equal_weights(),
            },
            {
                "code": "entropy",
                "title": _("По различительной способности"),
                "note": _("веса выведены из данных методом энтропии"),
                "weights": entropy_weights(base_rows),
            },
        ]
        for scheme in schemes:
            scheme.update(_compare_ranking(base, load_index(scheme["weights"])))

        perturbations = []
        for item in COMPONENTS:
            for direction, label in ((1, _("выше")), (-1, _("ниже"))):
                weights = dict(INDEX_WEIGHTS)
                weights[item.key] = max(
                    weights[item.key] * (1 + direction * WEIGHT_PERTURBATION), 0.0
                )
                outcome = _compare_ranking(base, load_index(_normalized_weights(weights)))
                perturbations.append(
                    {
                        "component": item,
                        "direction": label,
                        "weight": round(_normalized_weights(weights)[item.key], 3),
                        "correlation": outcome["correlation"],
                        "max_shift": outcome["max_shift"],
                        "leader": outcome["leader"],
                    }
                )

        columns = {
            item.key: [_share(row, item.key) for row in base_rows] for item in COMPONENTS
        }
        correlations = [
            {
                "first": first,
                "second": second,
                "value": round(pearson(columns[first.key], columns[second.key]), 2),
            }
            for position, first in enumerate(COMPONENTS)
            for second in COMPONENTS[position + 1:]
        ]

        return {
            "available": True,
            "base": base_rows,
            "schemes": schemes,
            "perturbations": perturbations,
            "correlations": sorted(
                correlations, key=lambda item: abs(item["value"]), reverse=True
            ),
            "worst_correlation": min(
                (scheme["correlation"] for scheme in schemes), default=1.0
            ),
            "largest_shift": max(
                [scheme["max_shift"] for scheme in schemes]
                + [item["max_shift"] for item in perturbations],
                default=0,
            ),
            "leaders": sorted(
                {scheme["leader"] for scheme in schemes}
                | {item["leader"] for item in perturbations}
            ),
            # В процентах: величина выводится в тексте испытания.
            "perturbation": round(WEIGHT_PERTURBATION * 100),
        }

    return _cached("analytics:sensitivity", build)


# ---------------------------------------------------------------------------
#  2. Типология округов (метод k-средних)
# ---------------------------------------------------------------------------


@dataclass
class ClusterResult:
    """Результат кластеризации: метки, центры и мера качества."""

    labels: list[int]
    centroids: list[list[float]]
    inertia: float
    iterations: int


def k_means(points: list[list[float]], k: int, seed: int = 42, max_iter: int = 100) -> ClusterResult:
    """Кластеризация методом k-средних с детерминированной инициализацией.

    Реализован вариант k-means++ для выбора начальных центров: он снижает
    вероятность попадания в неудачный локальный минимум по сравнению со
    случайной инициализацией. Генератор случайных чисел зафиксирован, поэтому
    результат воспроизводим — это существенно для проверяемости работы.
    """
    if not points or k <= 0:
        return ClusterResult([], [], 0.0, 0)
    k = min(k, len(points))
    rng = random.Random(seed)

    # --- Инициализация k-means++ -------------------------------------------
    centroids = [list(points[rng.randrange(len(points))])]
    while len(centroids) < k:
        distances = [min(_sq_distance(p, c) for c in centroids) for p in points]
        total = sum(distances)
        if total <= 0:
            centroids.append(list(points[rng.randrange(len(points))]))
            continue
        threshold = rng.random() * total
        cumulative = 0.0
        for point, distance in zip(points, distances, strict=False):
            cumulative += distance
            if cumulative >= threshold:
                centroids.append(list(point))
                break

    labels = [0] * len(points)
    # Счётчик ведётся явно: значение требуется после выхода из цикла, а
    # использование переменной цикла за его пределами затрудняет чтение.
    iterations = 0

    # --- Итерации Ллойда ----------------------------------------------------
    for step in range(1, max_iter + 1):
        iterations = step
        moved = False
        for index, point in enumerate(points):
            best = min(range(len(centroids)), key=lambda c: _sq_distance(point, centroids[c]))
            if labels[index] != best:
                labels[index] = best
                moved = True

        for cluster in range(len(centroids)):
            members = [p for p, label in zip(points, labels, strict=False) if label == cluster]
            if members:
                centroids[cluster] = [
                    sum(values) / len(members) for values in zip(*members, strict=False)
                ]

        if not moved:
            break

    inertia = sum(_sq_distance(p, centroids[label]) for p, label in zip(points, labels, strict=False))
    return ClusterResult(labels, centroids, inertia, iterations)


def _sq_distance(a: list[float], b: list[float]) -> float:
    """Квадрат евклидова расстояния между точками признакового пространства."""
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))


def _share(row: dict, key: str) -> float:
    """Нормированная составляющая округа для признакового пространства.

    Неопределённая составляющая заменяется серединой шкалы: это ровно то
    положение, которое не сдвигает округ ни к одной из групп.
    """
    value = row["shares"].get(key)
    return 0.5 if value is None else value


def silhouette(points: list[list[float]], labels: list[int]) -> float:
    """Средний силуэт разбиения — мера его обоснованности.

    Для каждого объекта сравнивается среднее расстояние до своей группы
    с наименьшим средним расстоянием до чужой. Значение, близкое к единице,
    означает, что объект лежит в своей группе и далеко от прочих; около нуля —
    что он равно принадлежит двум; отрицательное — что отнесён не туда.

    Величина отвечает на вопрос, на который сумма внутригрупповых расстояний
    ответить не может: она убывает с ростом числа групп всегда, и по ней
    нельзя отличить обоснованное разбиение от дробления выборки.
    """
    if len(points) < 3 or len(set(labels)) < 2:
        return 0.0

    members: dict[int, list[int]] = {}
    for position, label in enumerate(labels):
        members.setdefault(label, []).append(position)

    total = 0.0
    for position, label in enumerate(labels):
        own = [other for other in members[label] if other != position]
        if not own:
            # Одиночная группа: сравнивать объект внутри неё не с чем,
            # и приписывать ему согласие с самим собой нельзя.
            continue
        inner = sum(
            math.sqrt(_sq_distance(points[position], points[other])) for other in own
        ) / len(own)
        outer = min(
            sum(
                math.sqrt(_sq_distance(points[position], points[other]))
                for other in group
            )
            / len(group)
            for other_label, group in members.items()
            if other_label != label
        )
        spread = max(inner, outer)
        total += (outer - inner) / spread if spread else 0.0

    return total / len(points)


def silhouette_verdict(score: float) -> str:
    """Словесная оценка выраженности групповой структуры.

    Границы приняты по общепринятому толкованию силуэта: до 0,25 группы
    неотличимы от произвольного разбиения, до 0,5 структура прослеживается,
    но слабо, выше 0,7 разделение отчётливое. Сообщать это обязательно:
    метод k-средних разложит на группы любую выборку, в том числе такую,
    в которой групп нет.
    """
    if score >= 0.7:
        return _("структура отчётливая")
    if score >= 0.5:
        return _("структура выражена")
    if score >= 0.25:
        return _("структура прослеживается слабо")
    return _("групповой структуры не обнаружено: разбиение условно")


#: Пределы перебора числа групп.
#:
#: Одна группа типологией не является, а двенадцать округов, разложенные
#: более чем на шесть групп, дают группы по одному-два округа: такое
#: разбиение описывает выборку, а не обобщает её.
CLUSTER_RANGE = range(2, 7)


def cluster_quality() -> dict:
    """Обосновать число групп типологии перебором.

    Для каждого допустимого числа групп считаются две величины: сумма
    внутригрупповых расстояний и средний силуэт. Первая убывает всегда
    и указывает лишь точку перелома — то место, после которого дробление
    перестаёт заметно улучшать разбиение. Вторая имеет максимум, и он
    и предлагается как обоснованное число групп.
    """

    def build() -> dict:
        points, _rows = _feature_space()
        if len(points) < 3:
            return {"available": False, "recommended": 0, "steps": []}

        steps = []
        previous: float | None = None
        for size in CLUSTER_RANGE:
            outcome = k_means(points, size)
            score = silhouette(points, outcome.labels)
            steps.append(
                {
                    "k": size,
                    "inertia": round(outcome.inertia, 3),
                    "silhouette": round(score, 3),
                    # Насколько дробление уменьшило разброс внутри групп:
                    # по этой величине и отыскивается точка перелома.
                    "gain": (
                        None
                        if previous is None or not previous
                        else round((previous - outcome.inertia) / previous * 100, 1)
                    ),
                    "sizes": sorted(
                        (outcome.labels.count(label) for label in set(outcome.labels)),
                        reverse=True,
                    ),
                }
            )
            previous = outcome.inertia

        best = max(steps, key=lambda step: step["silhouette"])
        for step in steps:
            step["recommended"] = step["k"] == best["k"]
        return {
            "available": True,
            "steps": steps,
            "recommended": best["k"],
            "score": best["silhouette"],
            "verdict": silhouette_verdict(best["silhouette"]),
        }

    return _cached("analytics:cluster_quality", build)


def cluster_name(centroid: list[float]) -> str:
    """Назвать группу по тому, чем она выделяется.

    Признаки стандартизованы, поэтому координаты центра прямо показывают,
    насколько группа отклоняется от среднего по городу. Группа, отклонений
    не имеющая, так и называется: приписывать ей черту, которой в данных
    нет, значило бы выдать разбиение за содержательный вывод.
    """
    keys = [item.key for item in COMPONENTS]
    pairs = dict(zip(keys, centroid, strict=False))
    if all(value <= -TRAIT_THRESHOLD for value in centroid):
        return _("Округа низкой нагрузки по всем составляющим")
    if all(value >= TRAIT_THRESHOLD for value in centroid):
        return _("Округа высокой нагрузки по всем составляющим")
    leading = max(pairs, key=lambda key: pairs[key])
    if pairs[leading] < TRAIT_THRESHOLD:
        return _("Округа без выраженного профиля")
    return CLUSTER_TRAITS[leading]


def _feature_space() -> tuple[list[list[float]], list[dict]]:
    """Признаковое пространство типологии и породившие его записи.

    Признаки стандартизуются: без этого расстояние определялось бы той
    составляющей, разброс которой шире, — а разброс зависит от единицы
    измерения, а не от содержания.
    """
    rows = load_index()
    if len(rows) < 2:
        return [], rows
    features = [item.key for item in COMPONENTS]
    columns = {key: z_scores([_share(row, key) for row in rows]) for key in features}
    return [[columns[key][i] for key in features] for i in range(len(rows))], rows


def typology(k: int = 4) -> dict:
    """Построить типологию округов по нормированным показателям.

    Признаковое пространство образуют стандартизованные составляющие
    композитного индекса — те же величины, по которым он считается.
    Стандартизация обязательна: без неё расстояние определялось бы почти
    исключительно концентрацией складских площадей, измеряемой тысячами
    квадратных метров на квадратный километр.
    """

    def build() -> dict:
        points, rows = _feature_space()
        if len(rows) < 2:
            return {"clusters": [], "rows": rows, "k": 0}

        features = [item.key for item in COMPONENTS]
        result = k_means(points, k)

        # Кластеры упорядочиваются по средней нагрузке, чтобы номер группы
        # имел содержательный смысл, а подписи оставались стабильными.
        order = sorted(
            range(len(result.centroids)),
            key=lambda c: sum(result.centroids[c]) / len(result.centroids[c]),
        )
        rank_of = {cluster: position for position, cluster in enumerate(order)}

        clusters: dict[int, dict] = {}
        for row, label in zip(rows, result.labels, strict=False):
            position = rank_of[label]
            bucket = clusters.setdefault(
                position,
                {
                    "index": position,
                    "name": cluster_name(result.centroids[label]),
                    "centroid": dict(
                        zip(features, result.centroids[label], strict=False)
                    ),
                    "members": [],
                    "avg_score": 0.0,
                },
            )
            bucket["members"].append(row)
            row["cluster"] = position
            row["cluster_name"] = bucket["name"]

        for bucket in clusters.values():
            scores = [member["score"] for member in bucket["members"]]
            bucket["avg_score"] = round(sum(scores) / len(scores), 1)
            bucket["size"] = len(bucket["members"])

        return {
            "clusters": [clusters[key] for key in sorted(clusters)],
            "rows": rows,
            "k": k,
            "inertia": round(result.inertia, 3),
            "silhouette": round(silhouette(points, result.labels), 3),
            "iterations": result.iterations,
            "features": [INDEX_COMPONENTS[f] for f in features],
        }

    return _cached(f"analytics:typology:{k}", build)


# ---------------------------------------------------------------------------
#  3. Прогнозирование грузопотока
# ---------------------------------------------------------------------------


def linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Оценить параметры линейной модели методом наименьших квадратов.

    Возвращает кортеж ``(свободный член, коэффициент наклона, R²)``.
    Коэффициент детерминации показывает долю дисперсии, объяснённую моделью.
    """
    n = len(xs)
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0, 0.0

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    variance = sum((x - mean_x) ** 2 for x in xs)
    slope = covariance / variance if variance else 0.0
    intercept = mean_y - slope * mean_x

    ss_total = sum((y - mean_y) ** 2 for y in ys)
    ss_residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=False))
    r_squared = 1 - ss_residual / ss_total if ss_total else 0.0
    return intercept, slope, r_squared


#: Наименьшее число наблюдений, при котором строится прогноз. Ряд короче
#: не позволяет ни выделить отложенную выборку, ни оценить надёжность тренда:
#: подгонка по трём точкам всегда выглядит безупречной и ничего не означает.
MIN_OBSERVATIONS = 8

#: Доля ряда, отводимая под отложенную выборку. Качество измеряется на ней,
#: а не на обучающей: ошибка на данных, по которым модель построена, оценивает
#: не точность прогноза, а гибкость модели.
HOLDOUT_SHARE = 0.2


def forecast_flow(territory: str | None = None, horizon: int = 5,
                  scope: str | None = None) -> dict:
    """Построить прогноз объёма перевозок по территории.

    Модель — линейный тренд; на помесячных рядах к нему добавляется сезонная
    составляющая, оценённая как среднее отклонение соответствующего месяца
    от линии тренда. Простая форма выбрана осознанно: на ряде в десятки
    наблюдений сложные модели дают неустойчивые оценки параметров и мнимую
    точность.

    Качество измеряется на отложенной выборке: модель обучается на начале ряда
    и проверяется на его хвосте, которого при обучении не видела. Ошибка,
    посчитанная на обучающих данных, характеризует не точность прогноза.
    """
    # Без указания территории берётся ряд по городу: ведомственный, если он
    # загружен, иначе внутригородской, собранный по округам.
    history = (
        selectors.flow_timeseries(territory, scope or FlowScope.ALL)
        if territory
        else selectors.city_flow_series(scope or FlowScope.ALL)
    )
    if len(history) < MIN_OBSERVATIONS:
        return {
            "available": False,
            "history": history,
            "territory": territory or selectors.CITY_TERRITORY,
            "reason": _(
                "Ряд короче %(minimum)d наблюдений: прогноз по нему "
                "не строится, а оценка его качества была бы недостоверной."
            ) % {"minimum": MIN_OBSERVATIONS},
        }

    annual = history[-1]["period_type"] == PeriodType.YEAR
    # Сезонность оценивается только там, где она наблюдаема: на годовом ряде
    # внутригодового профиля нет, а на помесячном нужны хотя бы два цикла.
    seasonal_model = not annual and len(history) >= 24

    holdout = max(2, round(len(history) * HOLDOUT_SHARE))
    train, test = history[:-holdout], history[-holdout:]

    trained = _fit(train, seasonal_model)
    mape = _mape(test, trained, offset=len(train))

    # Прогноз строится по всему ряду: отложенная выборка нужна для проверки,
    # а не для того, чтобы выбросить последние наблюдения из модели.
    model = _fit(history, seasonal_model)
    predictions = _predict(history, model, horizon, annual)

    return {
        "available": True,
        "history": history,
        "forecast": predictions,
        "territory": territory or selectors.CITY_TERRITORY,
        "granularity": PeriodType.YEAR if annual else history[-1]["period_type"],
        "seasonal_model": seasonal_model,
        "slope": round(model["slope"], 1),
        "r_squared": round(model["r_squared"], 3),
        "mape": mape,
        "holdout": len(test),
        "horizon": horizon,
        "step_growth": round(model["slope"], 1),
        "seasonal": {month: round(value, 1) for month, value in sorted(model["seasonal"].items())},
        "quality": _quality_label(model["r_squared"], mape),
    }


def _fit(rows: list[dict], seasonal_model: bool) -> dict:
    """Оценить параметры модели по ряду наблюдений."""
    xs = list(range(len(rows)))
    ys = [row["volume"] for row in rows]
    intercept, slope, r_squared = linear_regression(xs, ys)

    seasonal: dict[int, float] = {}
    if seasonal_model:
        residuals: dict[int, list[float]] = {}
        for position, row in enumerate(rows):
            trend = intercept + slope * position
            residuals.setdefault(row["period"].month, []).append(row["volume"] - trend)
        seasonal = {
            month: sum(values) / len(values) for month, values in residuals.items()
        }

    return {
        "intercept": intercept,
        "slope": slope,
        "r_squared": r_squared,
        "seasonal": seasonal,
    }


def _value_at(model: dict, position: int, month: int) -> float:
    """Значение модели в заданной точке ряда."""
    trend = model["intercept"] + model["slope"] * position
    return max(trend + model["seasonal"].get(month, 0.0), 0.0)


def _mape(rows: list[dict], model: dict, offset: int) -> float | None:
    """Средняя абсолютная процентная ошибка на отложенной выборке."""
    errors = []
    for step, row in enumerate(rows):
        fitted = _value_at(model, offset + step, row["period"].month)
        if row["volume"]:
            errors.append(abs(row["volume"] - fitted) / row["volume"])
    return round(sum(errors) / len(errors) * 100, 1) if errors else None


def _predict(history: list[dict], model: dict, horizon: int, annual: bool) -> list[dict]:
    """Построить продолжение ряда на заданное число шагов."""
    last: date = history[-1]["period"]
    predictions = []
    for step in range(1, horizon + 1):
        position = len(history) - 1 + step
        if annual:
            period = date(last.year + step, last.month, 1)
        else:
            month = (last.month - 1 + step) % 12 + 1
            period = date(last.year + (last.month - 1 + step) // 12, month, 1)
        trend = model["intercept"] + model["slope"] * position
        predictions.append(
            {
                "period": period,
                # Ключ сохранён ради общего построителя графиков.
                "month": period,
                "value": round(_value_at(model, position, period.month), 1),
                "trend": round(max(trend, 0.0), 1),
            }
        )
    return predictions


def _quality_label(r_squared: float, mape: float | None) -> str:
    """Словесная оценка качества аппроксимации."""
    if mape is None:
        return _("не определено")
    if mape < 10 and r_squared > 0.6:
        return _("высокое")
    if mape < 20:
        return _("приемлемое")
    return _("низкое — прогноз носит ориентировочный характер")


# ---------------------------------------------------------------------------
#  4. Сценарное моделирование
# ---------------------------------------------------------------------------


#: Составляющие, которыми управляет сценарный расчёт, и подписи их рычагов.
#:
#: Плотность жилой застройки в состав не входит: она изменяется вместе
#: с городом за годы, а не решением о размещении объекта, и предлагать
#: её как условие сценария значило бы обещать несуществующий рычаг.
SCENARIO_LEVERS: dict[str, str] = {
    "storage": _("Складские площади"),
    "network": _("Протяжённость магистральной сети"),
    "restrictions": _("Число работ, затрагивающих грузовое движение"),
}

#: Пояснение к рычагу: какое решение стоит за изменением величины.
SCENARIO_HINTS: dict[str, str] = {
    "storage": _("Ввод новых складских объектов или вывод существующих"),
    "network": _("Строительство магистралей или закрытие направлений"),
    "restrictions": _("Развёртывание или завершение программы ремонтов"),
}


def scenario(district_id: int | None = None, **changes: float) -> dict:
    """Пересчитать индекс нагрузки при изменении условий в одном округе.

    Аргументы задают относительное изменение (в процентах) исходных величин
    составляющих: складских площадей, протяжённости магистральной сети, числа
    работ на ней. Изменённые величины проходят тот же расчёт, что и исходные:
    переносится сдвиг условий, а не отклик на него, — коэффициентов отклика,
    подтверждённых наблюдениями по городу, не существует.

    Условие задаётся по одному округу, и это не ограничение реализации.
    Нормирование относительное: изменение, равное во всех округах сразу,
    сдвигает все величины разом и оставляет расстановку прежней. Вопрос,
    на который расчёт отвечает, звучит иначе — что изменится, если объект
    разместить здесь.
    """
    base = load_index()
    if not base:
        return {"available": False}

    target = next(
        (row["district"] for row in base if row["district"].id == district_id),
        base[0]["district"],
    )
    factors = {
        key: 1 + float(changes.get(key, 0.0)) / 100 for key in SCENARIO_LEVERS
    }

    shifted = []
    for item in base:
        touched = item["district"].id == target.id
        values = (
            {
                key: None if value is None else value * factors.get(key, 1.0)
                for key, value in item["raw"].items()
            }
            if touched
            else dict(item["raw"])
        )
        shifted.append(
            {"district": item["district"], "values": values, "target": touched,
             "base_score": item["score"], "base_rank": item["rank"]}
        )

    normalized = {
        item.key: _normalize_column(
            [row["values"][item.key] for row in shifted], item.inverse
        )
        for item in COMPONENTS
    }
    for position, row in enumerate(shifted):
        shares = {item.key: normalized[item.key][position] for item in COMPONENTS}
        row["score"] = round(_score_row(shares, INDEX_WEIGHTS) * 100, 1)
        row["delta"] = round(row["score"] - row["base_score"], 1)

    shifted.sort(key=lambda row: row["score"], reverse=True)
    for rank, row in enumerate(shifted, start=1):
        row["rank"] = rank
        row["rank_delta"] = row["base_rank"] - rank

    subject = next(row for row in shifted if row["target"])
    return {
        "available": True,
        "rows": shifted,
        "district": target,
        "subject": subject,
        "params": {key: float(changes.get(key, 0.0)) for key in SCENARIO_LEVERS},
        "levers": SCENARIO_LEVERS,
        "avg_delta": round(sum(row["delta"] for row in shifted) / len(shifted), 2),
        "worsened": sum(1 for row in shifted if row["delta"] > 0.5),
        "improved": sum(1 for row in shifted if row["delta"] < -0.5),
        "reordered": sum(1 for row in shifted if row["rank_delta"]),
    }


def compare_districts(ids: list[int]) -> dict:
    """Сопоставить профили выбранных округов по составляющим индекса."""
    rows = {row["district"].id: row for row in load_index()}
    selected = [rows[i] for i in ids if i in rows]
    if not selected:
        return {"available": False, "rows": []}

    return {
        "available": True,
        "rows": selected,
        "components": [
            {
                "key": item.key,
                "title": item.title,
                "unit": item.unit,
                "weight": item.weight,
                "inverse": item.inverse,
                "values": [row["components"][item.key] for row in selected],
                "leader": max(
                    selected, key=lambda r: _share(r, item.key)
                )["district"].short_name,
            }
            for item in COMPONENTS
        ],
        "best": max(selected, key=lambda row: row["score"]),
        "worst": min(selected, key=lambda row: row["score"]),
    }


def _cached(key: str, builder):
    """Кешировать результат расчёта на срок, заданный настройками."""
    value = cache.get(key)
    if value is None:
        value = builder()
        cache.set(key, value, settings.ANALYTICS_CACHE_TTL)
    return value


def invalidate() -> None:
    """Сбросить кеш аналитических расчётов после обновления данных."""
    cache.delete("analytics:load_index")
    cache.delete("analytics:sensitivity")
    cache.delete("analytics:cluster_quality")
    for k in CLUSTER_RANGE:
        cache.delete(f"analytics:typology:{k}")

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

#: Названия типологических групп в порядке возрастания нагрузки.
CLUSTER_NAMES: tuple[str, ...] = (
    _("Периферийные округа с низкой нагрузкой"),
    _("Округа сбалансированного профиля"),
    _("Округа концентрации складских мощностей"),
    _("Округа предельной транспортной нагрузки"),
)


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


def typology(k: int = 4) -> dict:
    """Построить типологию округов по нормированным показателям.

    Признаковое пространство образуют стандартизованные составляющие
    композитного индекса — те же величины, по которым он считается.
    Стандартизация обязательна: без неё расстояние определялось бы почти
    исключительно концентрацией складских площадей, измеряемой тысячами
    квадратных метров на квадратный километр.
    """

    def build() -> dict:
        rows = load_index()
        if len(rows) < 2:
            return {"clusters": [], "rows": rows, "k": 0}

        features = [item.key for item in COMPONENTS]
        columns = {
            key: z_scores([_share(row, key) for row in rows]) for key in features
        }
        points = [[columns[key][i] for key in features] for i in range(len(rows))]

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
                    "name": CLUSTER_NAMES[min(position, len(CLUSTER_NAMES) - 1)],
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
    for k in range(2, 7):
        cache.delete(f"analytics:typology:{k}")

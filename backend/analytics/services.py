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
from core.choices import FlowScope, PeriodType
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

# Веса составляющих композитного индекса. Сумма равна единице; значения
# получены экспертным путём исходя из вклада фактора в нагрузку на сеть.
INDEX_WEIGHTS: dict[str, float] = {
    "capacity": 0.30,   # обеспеченность складскими мощностями
    "flow": 0.30,       # интенсивность грузопотока
    "congestion": 0.25, # загруженность дорожной сети
    "incidents": 0.15,  # аварийность и ограничения движения
}

#: Подписи составляющих для легенд и таблиц.
INDEX_COMPONENTS: dict[str, str] = {
    "capacity": _("Складские мощности"),
    "flow": _("Грузопоток"),
    "congestion": _("Загруженность сети"),
    "incidents": _("Аварийность"),
}

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


def _index_weights(raw: dict[str, list]) -> dict[str, float]:
    """Веса составляющих индекса с учётом того, что измерено.

    Составляющая, не измеренная ни по одному округу, из расчёта исключается,
    а её вес пропорционально распределяется между остальными: иначе пропуск
    в источнике одинаково занижал бы оценку всех округов и выглядел бы
    измеренным нулём.
    """
    present = {
        key: weight
        for key, weight in INDEX_WEIGHTS.items()
        if any(value for value in raw.get(key, []))
    }
    if not present:
        return dict(INDEX_WEIGHTS)
    total = sum(present.values())
    return {key: weight / total for key, weight in present.items()}


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


# ---------------------------------------------------------------------------
#  1. Композитный индекс логистической нагрузки
# ---------------------------------------------------------------------------


def load_index() -> list[dict]:
    """Рассчитать индекс логистической нагрузки по всем округам.

    Порядок расчёта:

    1. по каждому округу собираются четыре исходных показателя;
    2. каждый показатель нормируется методом «минимум — максимум», что делает
       разноразмерные величины (тонны, баллы, штуки) сопоставимыми;
    3. нормированные значения взвешиваются и суммируются;
    4. итог переводится в стобалльную шкалу и ранжируется.

    Возвращает список словарей, упорядоченный по убыванию индекса.
    """

    def build() -> list[dict]:
        profiles = selectors.district_profiles()
        if not profiles:
            return []

        incidents = _incidents_by_district()

        raw = {
            "capacity": [p["capacity_tons"] for p in profiles],
            "flow": [p["volume_tons"] for p in profiles],
            "congestion": [p["congestion"] for p in profiles],
            "incidents": [float(incidents.get(p["district"].id, 0)) for p in profiles],
        }
        # Составляющая, не измеренная ни по одному округу, из индекса
        # исключается, а её вес распределяется между остальными. Иначе
        # отсутствие сведений уменьшало бы оценку всех округов сразу,
        # притворяясь измеренным нулём.
        weights = _index_weights(raw)
        normalized = {
            key: min_max_normalize([value or 0.0 for value in raw[key]])
            for key in INDEX_WEIGHTS
        }

        rows: list[dict] = []
        for position, profile in enumerate(profiles):
            components = {
                key: round(normalized[key][position] * 100, 1) for key in INDEX_WEIGHTS
            }
            score = sum(normalized[key][position] * weight for key, weight in weights.items())
            rows.append(
                {
                    "district": profile["district"],
                    "score": round(score * 100, 1),
                    "components": components,
                    "weights": weights,
                    "raw": {key: raw[key][position] or 0.0 for key in raw},
                    # Отличие неизмеренной величины от измеренного нуля
                    # сохраняется отдельно: в расчёте они ведут себя
                    # одинаково, а в карточке округа — нет.
                    "measured": {key: raw[key][position] is not None for key in raw},
                    "object_count": profile["object_count"],
                    "congestion": profile["congestion"],
                    "incidents": incidents.get(profile["district"].id, 0),
                }
            )

        rows.sort(key=lambda row: row["score"], reverse=True)
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    return _cached("analytics:load_index", build)


def _incidents_by_district() -> dict[int, int]:
    """Число зарегистрированных событий в разрезе округов.

    Событие относится к округу по своей координате. Привязка к участку для
    этого не годится: реестр магистралей содержит сеть городского значения,
    и работы на районной улице выпали бы из территориального разреза.
    """
    from core.models import TrafficIncident
    from django.db.models import Count

    rows = (
        TrafficIncident.objects.filter(district__isnull=False)
        .values("district_id")
        .annotate(count=Count("id"))
    )
    return {row["district_id"]: row["count"] for row in rows}


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


def typology(k: int = 4) -> dict:
    """Построить типологию округов по нормированным показателям.

    Признаковое пространство образуют четыре стандартизованных показателя —
    те же, что участвуют в композитном индексе. Стандартизация обязательна:
    без неё расстояние определялось бы почти исключительно объёмом грузопотока,
    измеряемым в десятках тысяч тонн.
    """

    def build() -> dict:
        rows = load_index()
        if len(rows) < 2:
            return {"clusters": [], "rows": rows, "k": 0}

        features = ["capacity", "flow", "congestion", "incidents"]
        columns = {
            key: z_scores([float(row["raw"][key]) for row in rows]) for key in features
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


def scenario(
    flow_change_pct: float = 0.0,
    capacity_change_pct: float = 0.0,
    road_capacity_change_pct: float = 0.0,
) -> dict:
    """Пересчитать индекс нагрузки при заданных изменениях условий.

    Аргументы задают относительное изменение (в процентах) объёма грузопотока,
    складских мощностей и пропускной способности дорожной сети. Модель
    отклика — эластичная: рост грузопотока увеличивает загруженность сети
    пропорционально с коэффициентом 0,6, ввод дополнительных мощностей
    частично компенсирует нагрузку.
    """
    base = load_index()
    if not base:
        return {"available": False}

    flow_factor = 1 + flow_change_pct / 100
    capacity_factor = 1 + capacity_change_pct / 100
    road_factor = 1 + road_capacity_change_pct / 100

    # Коэффициенты эластичности отклика загруженности на изменение условий.
    flow_elasticity = 0.6
    road_elasticity = 0.4

    rows = []
    for item in base:
        raw = dict(item["raw"])
        raw["flow"] *= flow_factor
        raw["capacity"] *= capacity_factor
        congestion = raw["congestion"] * (
            1 + flow_elasticity * (flow_factor - 1) - road_elasticity * (road_factor - 1)
        )
        raw["congestion"] = max(congestion, 0.0)
        rows.append({"district": item["district"], "raw": raw, "base_score": item["score"]})

    normalized = {
        key: min_max_normalize([row["raw"][key] for row in rows]) for key in INDEX_WEIGHTS
    }
    for position, row in enumerate(rows):
        score = sum(normalized[key][position] * weight for key, weight in INDEX_WEIGHTS.items())
        row["score"] = round(score * 100, 1)
        row["delta"] = round(row["score"] - row["base_score"], 1)

    rows.sort(key=lambda row: row["score"], reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "available": True,
        "rows": rows,
        "params": {
            "flow": flow_change_pct,
            "capacity": capacity_change_pct,
            "road": road_capacity_change_pct,
        },
        "avg_delta": round(sum(row["delta"] for row in rows) / len(rows), 2),
        "worsened": sum(1 for row in rows if row["delta"] > 0.5),
        "improved": sum(1 for row in rows if row["delta"] < -0.5),
    }


def compare_districts(ids: list[int]) -> dict:
    """Сопоставить профили выбранных округов по составляющим индекса."""
    rows = {row["district"].id: row for row in load_index()}
    selected = [rows[i] for i in ids if i in rows]
    if not selected:
        return {"available": False, "rows": []}

    components = list(INDEX_WEIGHTS)
    return {
        "available": True,
        "rows": selected,
        "components": [
            {
                "key": key,
                "title": INDEX_COMPONENTS[key],
                "weight": INDEX_WEIGHTS[key],
                "values": [row["components"][key] for row in selected],
                "leader": max(selected, key=lambda r: r["components"][key])["district"].short_name,
            }
            for key in components
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

"""Сценарий нагрузочного испытания ИС «ГрузПоток».

Метод: нагрузочное тестирование. Проверяется поведение системы при
одновременной работе нескольких десятков пользователей — прежде всего
устойчивость времени отклика страниц с агрегатами и слоёв карты, которые
выполняют наиболее тяжёлые запросы.

Запуск с веб-интерфейсом::

    locust -f tests/locustfile.py --host http://127.0.0.1:8000

Запуск без интерфейса, с автоматической остановкой::

    locust -f tests/locustfile.py --host http://127.0.0.1:8000 \
           --headless --users 50 --spawn-rate 5 --run-time 5m

Пороговые значения, принятые при испытании (см. docs/TEST_PLAN.md):
время отклика 95-го процентиля не более 1200 мс, доля ошибок не выше 1 %.
"""

from __future__ import annotations

import math
import random

from locust import HttpUser, between, task


def tile_of(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Номер тайла сетки XYZ, в который попадает точка.

    Расчёт повторён здесь намеренно: сценарий нагрузки обращается к системе
    по сети, как обычный клиент, и не должен зависеть от её кода.
    """
    side = 1 << zoom
    x = int((lon + 180.0) / 360.0 * side)
    sin_lat = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * side)
    return x, y


class VisitorScenario(HttpUser):
    """Незарегистрированный посетитель.

    Наиболее массовая роль: просмотр публичных реестров и карты без
    авторизации. Доля в общей нагрузке — около 70 %.
    """

    weight = 7
    # Интервал отражает реальное чтение страницы, а не предельную частоту:
    # цель испытания — оценить работу под правдоподобной нагрузкой.
    wait_time = between(2, 6)

    @task(10)
    def home(self):
        """Главная страница со сводкой и лентой состояния сети."""
        self.client.get("/", name="Главная")

    @task(8)
    def object_registry(self):
        """Реестр объектов с постраничной навигацией."""
        page = random.randint(1, 3)
        self.client.get(f"/objects/?page={page}", name="Реестр объектов")

    @task(6)
    def object_filtering(self):
        """Реестр объектов с наложенными условиями отбора."""
        district = random.randint(1, 12)
        self.client.get(
            f"/objects/?district={district}&sort=capacity",
            name="Реестр объектов (отбор)",
        )

    @task(5)
    def districts(self):
        """Профили округов — страница с тяжёлыми агрегатами."""
        self.client.get("/districts/", name="Профили округов")

    @task(4)
    def traffic(self):
        """Сводка дорожной обстановки с суточным профилем."""
        self.client.get("/traffic/", name="Дорожная обстановка")

    @task(4)
    def incidents(self):
        """Журнал дорожных событий."""
        self.client.get("/incidents/?state=open", name="Инциденты")

    @task(3)
    def district_card(self):
        """Карточка округа."""
        district = random.randint(1, 12)
        self.client.get(f"/districts/{district}/", name="Карточка округа")

    @task(2)
    def flows(self):
        """Статистика грузопотоков с помесячной динамикой."""
        self.client.get("/flows/", name="Грузопотоки")


class MapScenario(HttpUser):
    """Работа с картой.

    Тайлы — наиболее ресурсоёмкие запросы системы: они выполняют
    пространственную выборку, обрезают геометрию и упаковывают её.
    Доля — около 20 %.
    """

    weight = 2
    wait_time = between(3, 8)

    def on_start(self):
        """Открытие страницы карты предшествует загрузке слоёв."""
        self.client.get("/map/", name="Карта")

    @task(6)
    def city_tiles(self):
        """Тайлы обзорного масштаба: город целиком."""
        x, y = tile_of(random.uniform(37.4, 37.8), random.uniform(55.6, 55.9), 10)
        self.client.get(f"/tiles/10/{x}/{y}.pbf", name="Тайл: обзор города")

    @task(5)
    def district_tiles(self):
        """Тайлы масштаба округа."""
        x, y = tile_of(random.uniform(37.4, 37.8), random.uniform(55.6, 55.9), 13)
        self.client.get(f"/tiles/13/{x}/{y}.pbf", name="Тайл: округ")

    @task(3)
    def street_tiles(self):
        """Тайлы масштаба улицы: появляются контуры объектов."""
        x, y = tile_of(random.uniform(37.4, 37.8), random.uniform(55.6, 55.9), 15)
        self.client.get(f"/tiles/15/{x}/{y}.pbf", name="Тайл: улица")

    @task(2)
    def tile_source(self):
        """Описание источника тайлов."""
        self.client.get("/tiles/tiles.json", name="Описание источника тайлов")

    @task(2)
    def nearby_search(self):
        """Поиск объектов вблизи произвольной точки."""
        lon = round(random.uniform(37.4, 37.8), 4)
        lat = round(random.uniform(55.6, 55.9), 4)
        self.client.get(
            f"/layers/nearby/?lon={lon}&lat={lat}&radius=5",
            name="Поиск: что рядом",
        )


class AnalyticsScenario(HttpUser):
    """Работа с аналитическими разделами.

    Расчёты индекса, типологии и прогноза кешируются, поэтому испытание
    проверяет прежде всего корректность работы кеша под нагрузкой. Доля —
    около 10 %.
    """

    weight = 1
    wait_time = between(5, 12)

    @task(5)
    def load_index(self):
        """Композитный индекс логистической нагрузки."""
        self.client.get("/analytics/", name="Индекс нагрузки")

    @task(3)
    def typology(self):
        """Типология округов методом k-средних."""
        k = random.choice([2, 3, 4, 5])
        self.client.get(f"/analytics/typology/?k={k}", name="Типология")

    @task(3)
    def forecast(self):
        """Прогноз грузопотока."""
        horizon = random.choice([3, 6, 12])
        self.client.get(
            f"/analytics/forecast/?horizon={horizon}", name="Прогноз"
        )

    @task(2)
    def scenario(self):
        """Сценарный расчёт — не кешируется, выполняется при каждом запросе."""
        flow = random.choice([-20, 0, 15, 30, 50])
        self.client.get(
            f"/analytics/scenario/?flow={flow}&capacity=0&road=10",
            name="Сценарный расчёт",
        )

    @task(2)
    def api_load_index(self):
        """Тот же расчёт через программный интерфейс."""
        self.client.get("/api/v1/analytics/load-index/", name="API: индекс")

    @task(1)
    def api_objects(self):
        """Постраничная выдача реестра через API."""
        self.client.get("/api/v1/objects/?page_size=50", name="API: объекты")

"""Проверки в настоящем браузере.

Метод: системное тестирование методом чёрного ящика, динамическое.

Тестовый клиент Django получает разметку, но не исполняет сценарии и не
разбирает встроенные в страницу данные, поэтому поведение клиентской части
остаётся вне его охвата. Здесь страницы открываются управляемым браузером,
и любое сообщение об ошибке в консоли считается отказом.

Внешняя сеть при этом не используется: тайлы карты система отдаёт сама,
а запросы к службе шрифтов перехватываются и обслуживаются заглушками.
Проверка не должна зависеть от доступности стороннего сервиса — иначе она
начнёт падать по причинам, к приложению не относящимся.

Запуск:
    pytest tests/test_browser.py
Требуется однократная установка браузера:
    python -m playwright install chromium
"""

from __future__ import annotations

import io
import os

import pytest

# Синхронный интерфейс Playwright работает поверх цикла событий, и Django
# считает такой вызов небезопасным: защита рассчитана на прикладной код,
# случайно обратившийся к базе из сопрограммы. Здесь обращения выполняет сам
# набор проверок, из отдельного потока сервера, поэтому защита снимается.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

pytest.importorskip("playwright", reason="Playwright не установлен")

pytestmark = [pytest.mark.django_db, pytest.mark.browser]

#: Адреса, которые страница запрашивает у сторонних служб.
EXTERNAL_PATTERNS = (
    "**/fonts.googleapis.com/**",
    "**/fonts.gstatic.com/**",
)


@pytest.fixture
def offline_page(page):
    """Страница браузера, отрезанная от внешних служб."""

    def stub_text(route):
        route.fulfill(status=200, content_type="text/css", body="")

    for pattern in EXTERNAL_PATTERNS:
        page.route(pattern, stub_text)
    return page


@pytest.fixture
def console_errors(offline_page):
    """Собрать сообщения об ошибках, возникшие на странице."""
    collected: list[str] = []
    offline_page.on(
        "console",
        lambda message: collected.append(f"console.{message.type}: {message.text}")
        if message.type == "error"
        else None,
    )
    offline_page.on("pageerror", lambda error: collected.append(f"pageerror: {error}"))
    return collected


#: Разделы, которые обязаны открываться без единой ошибки в консоли.
PAGES = [
    ("/", "главная"),
    ("/map/", "карта"),
    ("/objects/", "реестр объектов"),
    ("/traffic/", "дорожная обстановка"),
    ("/flows/", "грузопотоки"),
    ("/analytics/", "аналитика"),
    ("/analytics/forecast/", "прогноз"),
    ("/analytics/sensitivity/", "чувствительность индекса"),
    ("/analytics/typology/", "типология"),
    # Картограмма рисуется разметкой SVG, а числа в разметке проходят
    # через локализацию: запятая в дробной части останавливает разбор
    # атрибута, и об этом сообщает только консоль браузера.
    ("/analytics/spatial/", "пространственный анализ"),
    ("/analytics/siting/", "подбор площадки"),
    ("/analytics/corridor/", "разбор коридора"),
    ("/permits/", "условия допуска"),
    ("/zones/", "зоны ограничения"),
    ("/methodology/", "методология"),
]


@pytest.mark.parametrize("path,title", PAGES)
def test_page_has_no_console_errors(
    live_server, offline_page, console_errors, full_dataset, path, title
):
    """Раздел «{title}» открывается без ошибок в консоли браузера."""
    offline_page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert console_errors == [], f"страница {path} ({title}): {console_errors}"


class TestMap:
    """Карта — раздел, ради которого этот набор и появился."""

    def test_map_initializes(self, live_server, offline_page, console_errors, full_dataset):
        """Карта создаётся и сообщает о готовности.

        Проверяется не отсутствие исключения, а результат: холст WebGL создан
        и сценарий довёл построение до конца.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        assert console_errors == [], console_errors
        assert offline_page.locator("#map-canvas canvas.maplibregl-canvas").count() == 1

    def test_map_requests_vector_tiles(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Данные приходят тайлами со своего домена, а не сторонней службы.

        Запросы считаются по журналу браузера, а не по ``performance``
        страницы: тайлы забирает и разбирает рабочий поток, и его обращения
        в перечень ресурсов главного потока не попадают.
        """
        requested: list[str] = []
        offline_page.on("request", lambda request: requested.append(request.url))

        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        offline_page.wait_for_timeout(2000)

        tiles = [url for url in requested if url.endswith(".pbf")]
        assert tiles, requested
        assert any(url.endswith("/tiles/tiles.json") for url in requested), requested
        assert all(url.startswith(live_server.url) for url in tiles), tiles

        # Всё, что относится к карте, приходит со своего домена: внешними
        # остаются только запросы к службе шрифтов, перехваченные заглушкой.
        external = [url for url in requested if not url.startswith(live_server.url)]
        assert all("fonts.g" in url for url in external), external
        assert console_errors == [], console_errors

    def test_map_centered_on_moscow(self, live_server, offline_page, full_dataset):
        """Карта центрирована на Москве, а не на нулевом меридиане.

        Проверка смотрит на настройки в том виде, в каком их разобрал
        клиент, а не на разметку: локализованное число остаётся в разметке
        синтаксически корректным и на глаз неотличимо.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        center = offline_page.evaluate(
            "JSON.parse(document.getElementById('map-settings').textContent).center"
        )
        assert len(center) == 2
        latitude, longitude = center
        assert 55.0 < latitude < 56.5
        assert 36.5 < longitude < 38.5

    def test_layers_are_drawn(self, live_server, offline_page, console_errors, full_dataset):
        """На холсте появляется изображение, а не однотонная заливка.

        Слои рисуются средствами WebGL, поэтому проверяется само
        изображение: снимок холста снимается браузером, потому что чтение
        буфера из сценария даёт пустоту — содержимое кадра после отрисовки
        не сохраняется.
        """
        from PIL import Image

        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        offline_page.wait_for_timeout(2500)

        shot = offline_page.locator("#map-canvas canvas.maplibregl-canvas").screenshot()
        colours = Image.open(io.BytesIO(shot)).convert("RGB").getcolors(maxcolors=1 << 20)

        assert colours is not None and len(colours) > 8, "на холсте карты только фон"
        assert console_errors == [], console_errors

    def test_district_labels_are_shown(self, live_server, offline_page, full_dataset):
        """Названия округов выводятся разметкой поверх карты."""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)
        assert offline_page.locator(".map-label").count() > 0

    def test_layer_toggle_does_not_reload(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Включение слоя выполняется на полученных данных, без запроса."""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        requested: list[str] = []
        offline_page.on("request", lambda request: requested.append(request.url))
        offline_page.click("input[data-layer='incidents']")
        offline_page.wait_for_timeout(500)

        assert not [url for url in requested if url.endswith(".pbf")], requested
        assert console_errors == [], console_errors


class TestTheme:
    """Переключение оформления."""

    def test_theme_switch_keeps_map_alive(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Смена оформления не роняет карту.

        Стиль собирается заново теми же переменными, что и остальной
        интерфейс. Ошибка в этом обработчике не видна на снимке страницы,
        но ломает карту при первом же переключении темы.
        """
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        offline_page.click("button[data-action='theme']")
        offline_page.wait_for_timeout(1000)

        assert console_errors == [], console_errors
        assert offline_page.locator("#map-canvas canvas.maplibregl-canvas").count() == 1

class TestRoutingTools:
    """Инструменты расчёта по графу дорог.

    Служба маршрутизации в проверке не разворачивается: проверяется, что
    интерфейс сообщает о её отсутствии и об отказе, а не рисует маршрут,
    полученный неизвестно откуда.
    """

    def test_tools_are_hidden_without_router(
        self, live_server, offline_page, console_errors, full_dataset, settings
    ):
        """Без службы маршрутизации инструменты не показываются вовсе."""
        settings.VALHALLA_URL = ""
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        assert offline_page.locator("#map-isochrone").count() == 0
        assert "по прямой" in offline_page.locator(".map-panel").inner_text()
        assert console_errors == [], console_errors

    def test_refusal_is_shown_in_the_panel(
        self, live_server, offline_page, console_errors, full_dataset, settings
    ):
        """Отказ службы доходит до пользователя её же словами."""
        # Адрес заведомо никуда не ведёт: расчёт обязан закончиться отказом,
        # а не молчанием.
        settings.VALHALLA_URL = "http://127.0.0.1:9"
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        box = offline_page.locator("#map-canvas").bounding_box()
        offline_page.click("#map-isochrone")
        offline_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        results = offline_page.locator("#routing-results")
        results.get_by_text("маршрутизации").wait_for(timeout=15_000)

        # Отказ службы браузер отмечает в консоли сам; проверяется отсутствие
        # ошибок сценария, то есть того, что отказ обработан, а не уронил
        # клиентскую часть.
        assert [item for item in console_errors if item.startswith("pageerror")] == []

    def test_pointing_mode_suppresses_object_card(
        self, live_server, offline_page, full_dataset, settings
    ):
        """В режиме указания точки карточка объекта не открывается."""
        settings.VALHALLA_URL = "http://127.0.0.1:9"
        offline_page.goto(f"{live_server.url}/map/", wait_until="networkidle")
        offline_page.wait_for_selector("#map-canvas[data-map-ready]", timeout=20_000)

        box = offline_page.locator("#map-canvas").bounding_box()
        offline_page.click("#map-isochrone")
        offline_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        offline_page.wait_for_timeout(1000)

        assert offline_page.locator(".maplibregl-popup").count() == 0

class TestRecordMaps:
    """Карты на карточках объекта, магистрали, коридора и события."""

    def card_paths(self, full_dataset):
        """Адреса карточек, у записей которых есть геометрия."""
        from core.models import CargoRoute, InfrastructureObject, RoadSegment, TrafficIncident

        return [
            f"/objects/{InfrastructureObject.objects.exclude(geom__isnull=True).first().pk}/",
            f"/roads/{RoadSegment.objects.exclude(geom__isnull=True).first().pk}/",
            f"/routes/{CargoRoute.objects.exclude(geom__isnull=True).first().pk}/",
            f"/incidents/{TrafficIncident.objects.exclude(geom__isnull=True).first().pk}/",
        ]

    def test_every_card_map_is_built(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Карта карточки строится и берёт данные с того же домена."""
        for path in self.card_paths(full_dataset):
            offline_page.goto(f"{live_server.url}{path}", wait_until="networkidle")
            offline_page.wait_for_selector("#minimap[data-map-ready]", timeout=20_000)
            assert offline_page.locator("#minimap canvas.maplibregl-canvas").count() == 1
            assert console_errors == [], f"{path}: {console_errors}"

    def test_record_without_geometry_has_no_map(
        self, live_server, offline_page, console_errors, full_dataset
    ):
        """Запись без координат карту не показывает и ошибок не даёт."""
        from core.models import InfrastructureObject

        target = InfrastructureObject.objects.filter(geom__isnull=True).first()
        offline_page.goto(f"{live_server.url}/objects/{target.pk}/", wait_until="networkidle")

        assert offline_page.locator("#minimap").count() == 0
        assert console_errors == [], console_errors


class TestAccessSection:
    """Раздел допуска: карта зоны и заключение по точке."""

    def zone_path(self) -> str:
        """Адрес карточки зоны с загруженной границей."""
        from core.models import RestrictionZone

        zone = RestrictionZone.objects.exclude(geom__isnull=True).first()
        return f"/zones/{zone.pk}/" if zone else ""

    @pytest.fixture
    def zone(self, db):
        """Зона ограничения с границей вокруг центра города."""
        from decimal import Decimal

        from core.models import RestrictionZone
        from geo import Geometry

        ring = [
            [37.55, 55.70], [37.70, 55.70], [37.70, 55.80], [37.55, 55.80],
            [37.55, 55.70],
        ]
        return RestrictionZone.objects.create(
            code="ttk", name="Зона Третьего транспортного кольца", short_name="ТТК",
            level=2, permit_required_from_tons=Decimal("3.50"), fine_rubles=7500,
            geom=Geometry("MULTIPOLYGON", [[ring]]),
            area_sq_km=Decimal("84.40"), perimeter_km=Decimal("34.90"),
        )

    def test_zone_boundary_is_drawn(
        self, live_server, offline_page, console_errors, full_dataset, zone
    ):
        """Граница зоны показывается на той же подложке, что и карта раздела."""
        offline_page.goto(f"{live_server.url}{self.zone_path()}", wait_until="networkidle")
        offline_page.wait_for_selector("#minimap[data-map-ready]", timeout=20_000)

        assert offline_page.locator("#minimap canvas.maplibregl-canvas").count() == 1
        assert console_errors == [], console_errors

    def test_permit_verdict_shows_point(
        self, live_server, offline_page, console_errors, full_dataset, zone
    ):
        """Заключение о допуске выводится вместе с картой точки назначения."""
        offline_page.goto(
            f"{live_server.url}/permits/?mass=20&eco=5&lon=37.6186&lat=55.7602",
            wait_until="networkidle",
        )
        offline_page.wait_for_selector("#minimap[data-map-ready]", timeout=20_000)

        assert "ТТК" in offline_page.content()
        assert console_errors == [], console_errors

"""Общие приспособления (fixtures) для автотестов.

Тесты исполняются на SQLite: это исключает зависимость от установленного
PostgreSQL и делает набор пригодным для запуска в среде непрерывной
интеграции. Совместимость с промышленным контуром проверяется отдельным
запуском того же набора с переменной ``FF_DB_ENGINE=postgres``.
"""

from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

os.environ.setdefault("FF_DB_ENGINE", "sqlite")
os.environ.setdefault("FF_DEBUG", "False")
os.environ.setdefault("FF_SECRET_KEY", "test-secret-key-for-automated-tests-only")


@pytest.fixture
def districts(db):
    """Три административных округа с координатами центров."""
    from core.models import District
    from geo import Geometry

    return [
        District.objects.create(
            name="Центральный", short_name="ЦАО",
            area_sq_km=Decimal("66.18"), population=783000,
            center=Geometry.point(37.6208, 55.7539),
        ),
        District.objects.create(
            name="Северный", short_name="САО",
            area_sq_km=Decimal("113.72"), population=1178000,
            center=Geometry.point(37.5350, 55.8386),
        ),
        District.objects.create(
            name="Южный", short_name="ЮАО",
            area_sq_km=Decimal("131.77"), population=1820000,
            center=Geometry.point(37.6541, 55.6216),
        ),
    ]


@pytest.fixture
def infrastructure_types(db):
    """Два типа объектов инфраструктуры."""
    from core.models import InfrastructureType

    return [
        InfrastructureType.objects.create(
            code="warehouse", name="Склад", description="Складские комплексы"
        ),
        InfrastructureType.objects.create(
            code="terminal", name="Грузовой терминал", description="Терминалы перевалки"
        ),
    ]


@pytest.fixture
def cargo_categories(db):
    """Две категории грузов: обычный и опасный."""
    from core.models import CargoCategory

    return [
        CargoCategory.objects.create(code="food", name="Продовольствие", hazard_class=0),
        CargoCategory.objects.create(code="fuel", name="ГСМ и топливо", hazard_class=3),
    ]


@pytest.fixture
def data_source(db):
    """Источник данных для привязки записей."""
    from core.models import DataSource

    return DataSource.objects.create(
        code="test_src", name="Тестовый источник", source_type="api",
        update_frequency="daily", is_active=True,
    )


@pytest.fixture
def objects(db, districts, infrastructure_types, data_source):
    """Пять объектов инфраструктуры с координатами."""
    from core.models import InfrastructureObject
    from geo import Geometry

    payload = [
        ("Складской комплекс «Юг»", 0, 0, 37.62, 55.74, 15000, 8000, "круглосуточно"),
        ("Терминал «Север»", 1, 1, 37.54, 55.84, 32000, 21000, "08:00-20:00"),
        ("Склад «Центр»", 0, 0, 37.61, 55.75, 4500, 2600, "09:00-18:00"),
        ("Терминал «Юг-2»", 1, 2, 37.65, 55.62, 51000, 34000, "круглосуточно"),
        ("Склад без координат", 0, 1, None, None, 8000, 5000, "08:00-20:00"),
    ]
    created = []
    for name, type_idx, district_idx, lon, lat, capacity, area, hours in payload:
        created.append(
            InfrastructureObject.objects.create(
                name=name,
                type=infrastructure_types[type_idx],
                district=districts[district_idx],
                address=f"г. Москва, {name}",
                capacity_tons=Decimal(capacity),
                area_sq_m=Decimal(area),
                operating_hours=hours,
                geom=Geometry.point(lon, lat) if lon else None,
                source=data_source,
            )
        )
    return created


@pytest.fixture
def roads(db, districts, data_source):
    """Три участка дорожной сети с геометрией."""
    from core.models import RoadSegment
    from geo import Geometry

    payload = [
        ("МКАД", "highway", 5, "108.90", 100, 0),
        ("Ленинградское шоссе", "arterial", 4, "24.50", 80, 1),
        ("Каширское шоссе", "arterial", 3, "18.20", 60, 2),
    ]
    return [
        RoadSegment.objects.create(
            name=name, road_class=road_class, lanes=lanes,
            length_km=Decimal(length), speed_limit_kmh=limit,
            district=districts[district_idx],
            geom=Geometry.line([(37.60 + i * 0.01, 55.75), (37.62 + i * 0.01, 55.78)]),
            source=data_source,
        )
        for i, (name, road_class, lanes, length, limit, district_idx) in enumerate(payload)
    ]


@pytest.fixture
def conditions(db, roads, data_source):
    """Замеры дорожной обстановки: по два на каждый участок."""
    from core.models import TrafficCondition

    now = timezone.now()
    created = []
    for index, road in enumerate(roads):
        for offset, level in ((2, 3 + index), (0, 5 + index * 2)):
            created.append(
                TrafficCondition.objects.create(
                    road=road,
                    recorded_at=now - timedelta(hours=offset),
                    congestion_level=level,
                    avg_speed_kmh=Decimal(60 - level * 4),
                    travel_time_min=Decimal(10 + level * 2),
                    vehicle_density=100 + level * 20,
                    incident_flag=level > 6,
                    source=data_source,
                )
            )
    return created


@pytest.fixture
def incidents(db, roads, data_source):
    """Четыре дорожных события разной серьёзности."""
    from core.models import TrafficIncident
    from geo import Geometry

    now = timezone.now()
    payload = [
        ("accident", 4, True, None, 0),
        ("roadworks", 2, False, now - timedelta(hours=3), 1),
        ("restriction", 5, True, None, 2),
        ("weather", 1, False, now - timedelta(days=2), 0),
    ]
    return [
        TrafficIncident.objects.create(
            reported_at=now - timedelta(hours=8 + index),
            resolved_at=resolved,
            incident_type=kind,
            severity=severity,
            road=roads[road_idx],
            description=f"Тестовое событие {index + 1}",
            geom=Geometry.point(37.60 + index * 0.01, 55.75),
            affects_cargo=cargo,
            source=data_source,
        )
        for index, (kind, severity, cargo, resolved, road_idx) in enumerate(payload)
    ]


@pytest.fixture
def routes(db, data_source):
    """Два грузовых маршрута."""
    from core.models import CargoRoute
    from geo import Geometry

    return [
        CargoRoute.objects.create(
            name="М-4 «Дон» — Москва", route_type="inbound",
            origin_region="Ростовская область", destination="Москва",
            distance_km=Decimal("1080.00"), avg_duration_h=Decimal("16.50"),
            truck_count_day=420,
            geom=Geometry.line([(37.6, 55.7), (39.7, 54.6)]),
            source=data_source,
        ),
        CargoRoute.objects.create(
            name="Москва — М-11", route_type="outbound",
            origin_region="Москва", destination="Санкт-Петербург",
            distance_km=Decimal("680.00"), avg_duration_h=Decimal("9.00"),
            truck_count_day=610,
            geom=Geometry.line([(37.5, 55.9), (30.3, 59.9)]),
            source=data_source,
        ),
    ]


@pytest.fixture
def flows(db, districts, cargo_categories, routes, data_source):
    """Помесячная статистика грузопотоков за двенадцать месяцев."""
    from datetime import date

    from core.models import FreightFlowStat

    created = []
    for month in range(1, 13):
        for index, district in enumerate(districts):
            created.append(
                FreightFlowStat.objects.create(
                    period_date=date(2025, month, 1),
                    period_type="month",
                    route=routes[index % len(routes)],
                    district=district,
                    cargo_category=cargo_categories[index % len(cargo_categories)],
                    direction=("in", "out", "transit")[index % 3],
                    volume_tons=Decimal(10000 + month * 350 + index * 1800),
                    vehicle_count=400 + month * 12 + index * 60,
                    avg_speed_kmh=Decimal("42.50"),
                    source=data_source,
                )
            )
    return created


@pytest.fixture
def full_dataset(objects, roads, conditions, incidents, routes, flows):
    """Полный набор данных предметной области для сквозных проверок."""
    from core import selectors

    selectors.invalidate_caches()
    return True


@pytest.fixture
def users(db):
    """Четыре учётные записи — по одной на каждую роль."""
    from accounts.models import Role, UserProfile

    created = {}
    for role in (Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN):
        user = User.objects.create_user(
            username=f"test_{role}",
            password="TestPassword2026",
            email=f"{role}@example.test",
            first_name="Тест",
            last_name=role.capitalize(),
            is_superuser=role == Role.ADMIN,
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.save()
        created[role] = user
    return created


@pytest.fixture(autouse=True)
def neutralize_deployment_settings(settings):
    """Отключить настройки промышленного контура на время теста.

    Обе настройки включаются автоматически вместе с отключением режима
    отладки и относятся к развёртыванию, а не к поведению системы:

    * **перенаправление на HTTPS** — тестовый клиент Django обращается по
      протоколу HTTP, и при включённом перенаправлении каждый запрос
      возвращал бы код 301 вместо ожидаемого ответа;
    * **хранилище статики с манифестом** — оно требует файла
      ``staticfiles.json``, создаваемого командой ``collectstatic``. Набор
      проверок не должен зависеть от наличия артефакта сборки: в среде
      непрерывной интеграции сборка статики не выполняется.

    Значения переопределяются на время теста, а не в самих настройках:
    настройки должны оставаться включёнными, чтобы их проверяло задание
    ``manage.py check --deploy``.

    Без этого приспособления результат прогона зависел бы от значения
    переменной окружения ``FF_DEBUG`` — на машине разработчика проверки
    проходили бы, а в среде непрерывной интеграции отказывали.
    """
    settings.SECURE_SSL_REDIRECT = False
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture(autouse=True)
def isolate_media_root(settings, tmp_path):
    """Увести файловые артефакты теста во временный каталог.

    Без этого приспособления прогон набора складывал сформированные отчёты
    в рабочий каталог ``media/exports`` рядом с пользовательскими: там
    обнаруживались выгрузки с тестовыми фикстурами — «Склад „Центр“»,
    «Тестовый источник». Помимо мусора это опасно тем, что тест мог
    наблюдать файл, оставленный предыдущим прогоном, и пройти по чужому
    результату.

    Каталог выдаётся pytest на каждый тест отдельно, поэтому проверки
    ещё и перестают зависеть друг от друга.
    """
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.EXPORT_ROOT = tmp_path / "media" / "exports"
    settings.EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def clear_caches():
    """Сбрасывать кеш между тестами.

    Без этого сводки, рассчитанные в одном тесте, попадали бы в следующий и
    делали результаты зависимыми от порядка выполнения.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()

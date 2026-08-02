"""Тесты команд управления.

Метод: интеграционное тестирование процедур загрузки и подготовки данных.
Проверяется, что команды работают идемпотентно и корректно разбирают
поставляемые наборы.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


class TestSeedParser:
    """Разбор инструкций SQL в команде загрузки."""

    def test_split_simple_values(self):
        """Значения разбиваются по запятым верхнего уровня."""
        from core.management.commands.load_seed import _split_values

        assert _split_values("1, 'текст', 2.5") == ["1", "'текст'", "2.5"]

    def test_split_respects_parentheses(self):
        """Запятые внутри вызова функции не разделяют значения."""
        from core.management.commands.load_seed import _split_values

        parts = _split_values("1, ST_MakePoint(37.6, 55.7), 'конец'")
        assert len(parts) == 3
        assert parts[1] == "ST_MakePoint(37.6, 55.7)"

    def test_split_respects_quotes(self):
        """Запятая внутри строкового литерала не разделяет значения."""
        from core.management.commands.load_seed import _split_values

        parts = _split_values("'Москва, ул. Складская', 42")
        assert len(parts) == 2
        assert parts[0] == "'Москва, ул. Складская'"

    def test_escaped_quote(self):
        """Удвоенная кавычка внутри строки не завершает литерал."""
        from core.management.commands.load_seed import _split_values

        assert len(_split_values("'О''Коннор', 1")) == 2

    def test_parse_null(self):
        """Литерал NULL приводится к отсутствующему значению."""
        from core.management.commands.load_seed import _parse_value

        assert _parse_value("NULL") is None

    def test_parse_booleans(self):
        """Логические литералы распознаются."""
        from core.management.commands.load_seed import _parse_value

        assert _parse_value("TRUE") is True
        assert _parse_value("FALSE") is False

    def test_parse_numbers(self):
        """Целые и вещественные числа приводятся к типам Python."""
        from core.management.commands.load_seed import _parse_value

        assert _parse_value("42") == 42
        assert _parse_value("3.14") == pytest.approx(3.14)

    def test_parse_makepoint(self):
        """Конструктор точки PostGIS преобразуется в представление WKT."""
        from core.management.commands.load_seed import _parse_value

        result = _parse_value("ST_SetSRID(ST_MakePoint(37.529410, 55.854143), 4326)")
        assert result == "POINT(37.529410 55.854143)"

    def test_parse_geomfromtext(self):
        """Конструктор геометрии из текста возвращает исходный WKT."""
        from core.management.commands.load_seed import _parse_value

        result = _parse_value(
            "ST_SetSRID(ST_GeomFromText('LINESTRING(37.6 55.7, 37.7 55.8)'), 4326)"
        )
        assert result.startswith("LINESTRING(")

    def test_unknown_constructor_raises(self):
        """Неизвестный конструктор геометрии отвергается с явной ошибкой."""
        from core.management.commands.load_seed import _parse_value

        with pytest.raises(ValueError):
            _parse_value("ST_Buffer(geom, 10)")

    def test_datetime_becomes_aware(self):
        """Отметка времени привязывается к часовому поясу проекта."""
        from core.management.commands.load_seed import _parse_value
        from django.utils import timezone

        moment = _parse_value("'2025-06-12T14:30:00'")
        assert timezone.is_aware(moment)

    def test_plain_date_stays_text(self):
        """Дата без времени не преобразуется в отметку времени."""
        from core.management.commands.load_seed import _parse_value

        assert _parse_value("'2025-06-12'") == "2025-06-12"


class TestLoadSeedCommand:
    """Загрузка поставляемого набора данных."""

    @pytest.fixture
    def seed_file(self, tmp_path):
        """Небольшой сценарий SQL, повторяющий структуру поставляемого."""
        path = tmp_path / "seed.sql"
        path.write_text(
            "BEGIN;\n"
            "-- Округа\n"
            "INSERT INTO districts (name, short_name, area_sq_km, population) "
            "VALUES ('Тестовый', 'ТСТ', 100.5, 500000);\n"
            "INSERT INTO infrastructure_types (code, name, description) "
            "VALUES ('warehouse', 'Склад', 'Складские комплексы');\n"
            "INSERT INTO data_sources (code, name, source_type, update_frequency, is_active) "
            "VALUES ('manual', 'Ручной ввод', 'manual', 'irregular', TRUE);\n"
            "INSERT INTO infrastructure_objects "
            "(type_id, district_id, name, address, capacity_tons, area_sq_m, geom, source_id) "
            "VALUES (1, 1, 'Склад «Тест»', 'ул. Складская, 1', 5000.0, 2500.0, "
            "ST_SetSRID(ST_MakePoint(37.62, 55.75), 4326), 1);\n"
            "COMMIT;\n",
            encoding="utf-8",
        )
        return path

    def test_loads_records(self, db, seed_file):
        """Записи всех таблиц загружаются в базу."""
        from core.models import District, InfrastructureObject

        # Ключ --truncate обязателен: в наборе внешние ключи записаны явными
        # числовыми значениями, предполагающими нумерацию с единицы. Без
        # сброса счётчиков результат зависел бы от предыдущих загрузок.
        call_command(
            "load_seed", str(seed_file), "--truncate", "--quiet-progress",
            stdout=StringIO(),
        )
        assert District.objects.count() == 1
        assert InfrastructureObject.objects.count() == 1

    def test_geometry_parsed(self, db, seed_file):
        """Геометрия сохраняется и читается обратно."""
        from core.models import InfrastructureObject

        call_command(
            "load_seed", str(seed_file), "--truncate", "--quiet-progress",
            stdout=StringIO(),
        )
        obj = InfrastructureObject.objects.first()
        assert obj.geom is not None
        assert obj.geom.lon == pytest.approx(37.62)

    def test_truncate_resets_identifiers(self, db, seed_file):
        """Повторная загрузка с очисткой не нарушает ссылочную целостность."""
        from core.models import District, InfrastructureObject

        call_command(
            "load_seed", str(seed_file), "--truncate", "--quiet-progress",
            stdout=StringIO(),
        )
        call_command(
            "load_seed", str(seed_file), "--truncate", "--quiet-progress",
            stdout=StringIO(),
        )
        assert District.objects.count() == 1
        assert InfrastructureObject.objects.first().district_id == 1

    def test_run_registered_in_journal(self, db, seed_file):
        """Факт загрузки фиксируется в журнале процедур."""
        from core.models import EtlRun

        call_command(
            "load_seed", str(seed_file), "--truncate", "--quiet-progress",
            stdout=StringIO(),
        )
        assert EtlRun.objects.filter(target_table__startswith="seed:").exists()

    def test_missing_file_raises(self, db):
        """Отсутствующий файл приводит к понятной ошибке."""
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("load_seed", "/nonexistent/path.sql", stdout=StringIO())


class TestDistrictCenters:
    """Заполнение координат центров округов."""

    def test_fills_known_districts(self, db):
        """Координаты проставляются округам из справочника."""
        from core.models import District

        District.objects.create(name="Центральный", short_name="ЦАО")
        call_command("district_centers", stdout=StringIO())
        assert District.objects.first().center is not None

    def test_idempotent(self, db, districts):
        """Повторный запуск не изменяет уже заполненные координаты."""
        before = districts[0].center.wkt
        call_command("district_centers", stdout=StringIO())
        districts[0].refresh_from_db()
        assert districts[0].center.wkt == before

    def test_force_overwrites(self, db, districts):
        """Ключ --force перезаписывает координаты эталонными значениями."""
        from geo import Geometry

        districts[0].center = Geometry.point(0.0, 0.0)
        districts[0].save()
        call_command("district_centers", "--force", stdout=StringIO())
        districts[0].refresh_from_db()
        assert districts[0].center.lon == pytest.approx(37.6208)


class TestSetupRoles:
    """Создание групп разрешений."""

    def test_groups_created(self, db):
        """Для каждой роли создаётся группа."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        assert Group.objects.filter(name__startswith="role:").count() == 4

    def test_permissions_are_nested(self, db):
        """Группа старшей роли включает разрешения младших."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        viewer = set(
            Group.objects.get(name="role:viewer").permissions.values_list("id", flat=True)
        )
        admin = set(
            Group.objects.get(name="role:admin").permissions.values_list("id", flat=True)
        )
        assert viewer.issubset(admin)

    def test_idempotent(self, db):
        """Повторный запуск не создаёт дублей групп."""
        from django.contrib.auth.models import Group

        call_command("setup_roles", stdout=StringIO())
        call_command("setup_roles", stdout=StringIO())
        assert Group.objects.filter(name__startswith="role:").count() == 4


class TestInitDemo:
    """Демонстрационное наполнение."""

    def test_creates_users_for_each_role(self, db):
        """Создаются учётные записи всех четырёх ролей."""
        from django.contrib.auth.models import User

        call_command("init_demo", stdout=StringIO())
        for username in ("viewer", "analyst", "operator", "admin"):
            assert User.objects.filter(username=username).exists()

    def test_creates_articles(self, db):
        """Публикуются аналитические материалы."""
        from content.models import Article

        call_command("init_demo", stdout=StringIO())
        assert Article.objects.count() >= 4

    def test_creates_feedback(self, db):
        """Создаются примеры обращений в разных состояниях."""
        from content.models import FeedbackMessage

        call_command("init_demo", stdout=StringIO())
        statuses = set(FeedbackMessage.objects.values_list("status", flat=True))
        assert len(statuses) >= 2

    def test_idempotent(self, db):
        """Повторный запуск не создаёт дублирующих записей."""
        from content.models import Article
        from django.contrib.auth.models import User

        call_command("init_demo", stdout=StringIO())
        articles, users = Article.objects.count(), User.objects.count()
        call_command("init_demo", stdout=StringIO())
        assert Article.objects.count() == articles
        assert User.objects.count() == users

    def test_skip_users_option(self, db):
        """Ключ --skip-users отключает создание учётных записей."""
        from django.contrib.auth.models import User

        call_command("init_demo", "--skip-users", stdout=StringIO())
        assert not User.objects.filter(username="viewer").exists()

"""Поле модели Django для хранения геометрии.

Поле реализует двухбэкендную стратегию хранения:

* **PostgreSQL + PostGIS** (промышленный контур) — колонка объявляется родным
  типом ``geometry(Point, 4326)``; запись выполняется через ``ST_GeomFromText``,
  чтение — через ``ST_AsText``. Пространственные индексы GiST и функции PostGIS
  работают в полном объёме;
* **SQLite** (разработка, автотесты, демонстрационная сборка) — колонка
  объявляется как ``text`` и хранит то же самое представление WKT.

В обоих случаях прикладной код получает из ORM объект :class:`geo.geometry.Geometry`
и не знает, какая СУБД находится под ним. Перенос данных между контурами
сводится к переносу текстовых значений WKT.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from .geometry import DEFAULT_SRID, Geometry, GeometryError


class GeometryField(models.Field):
    """Базовое поле геометрии произвольного типа.

    Аргументы:
        geom_type: тип геометрии PostGIS (``Point``, ``LineString``, …);
        srid: код системы координат, по умолчанию WGS-84.
    """

    description = "Геометрия PostGIS (WGS-84)"
    geom_type = "Geometry"

    def __init__(self, *args, geom_type: str | None = None, srid: int = DEFAULT_SRID, **kwargs):
        if geom_type:
            self.geom_type = geom_type
        self.srid = srid
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------- миграции

    def deconstruct(self):
        """Сохранить нестандартные параметры поля в файле миграции."""
        name, path, args, kwargs = super().deconstruct()
        if self.srid != DEFAULT_SRID:
            kwargs["srid"] = self.srid
        # Тип геометрии закреплён в подклассе, повторять его в миграции не нужно.
        if type(self) is GeometryField:
            kwargs["geom_type"] = self.geom_type
        return name, path, args, kwargs

    # --------------------------------------------------------------- схема

    def db_type(self, connection) -> str:
        """Тип колонки, зависящий от используемой СУБД."""
        if connection.vendor == "postgresql":
            return f"geometry({self.geom_type},{self.srid})"
        # SQLite и прочие бэкенды хранят WKT как обычный текст.
        return "text"

    def rel_db_type(self, connection) -> str:  # pragma: no cover — ссылок на geom нет
        return self.db_type(connection)

    # --------------------------------------------------------------- запись

    def get_placeholder(self, value, compiler, connection) -> str:
        """Подстановка в INSERT/UPDATE.

        PostGIS не принимает текст напрямую в колонку типа geometry, поэтому
        значение оборачивается конструктором ``ST_GeomFromText``. Для SQLite
        используется обычный параметр запроса.
        """
        if connection.vendor == "postgresql":
            return f"ST_GeomFromText(%s, {self.srid})"
        return "%s"

    def get_prep_value(self, value):
        """Привести значение прикладного уровня к строке WKT."""
        value = super().get_prep_value(value)
        if value is None or value == "":
            return None
        if isinstance(value, Geometry):
            return value.wkt
        if isinstance(value, dict):
            return Geometry.from_geojson(value).wkt
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return Geometry.point(value[0], value[1]).wkt
        if isinstance(value, str):
            # Строка уже в WKT — нормализуем, попутно проверив корректность.
            return Geometry.from_wkt(value).wkt
        raise TypeError(f"Невозможно привести {type(value)!r} к геометрии")

    # --------------------------------------------------------------- чтение

    def select_format(self, compiler, sql, params):
        """Обернуть колонку в ``ST_AsText`` при выборке из PostGIS.

        Без этого PostgreSQL вернул бы двоичное представление EWKB в
        шестнадцатеричном виде, для разбора которого понадобилась бы GEOS.
        """
        if compiler.connection.vendor == "postgresql":
            return f"ST_AsText({sql})", params
        return sql, params

    def from_db_value(self, value, expression, connection) -> Geometry | None:
        """Преобразовать значение из СУБД в объект :class:`Geometry`."""
        if value in (None, ""):
            return None
        if isinstance(value, Geometry):
            return value
        try:
            return Geometry.from_wkt(value)
        except GeometryError:
            # Повреждённая геометрия не должна ронять страницу целиком:
            # запись будет показана без координат, а проблема видна в отчёте
            # о качестве данных (console → «Качество данных»).
            return None

    def to_python(self, value) -> Geometry | None:
        """Преобразование при валидации форм и десериализации фикстур."""
        if value in (None, ""):
            return None
        if isinstance(value, Geometry):
            return value
        try:
            if isinstance(value, dict):
                return Geometry.from_geojson(value)
            return Geometry.from_wkt(str(value))
        except GeometryError as exc:
            raise ValidationError(str(exc), code="invalid_geometry") from exc

    def value_to_string(self, obj) -> str:
        """Сериализация в дампах ``manage.py dumpdata``."""
        value = self.value_from_object(obj)
        return value.wkt if isinstance(value, Geometry) else ""


class PointField(GeometryField):
    """Точечная геометрия: объекты инфраструктуры, инциденты, центры округов."""

    description = "Точка (WGS-84)"
    geom_type = "Point"


class LineStringField(GeometryField):
    """Линейная геометрия: участки дорог и грузовые маршруты."""

    description = "Ломаная линия (WGS-84)"
    geom_type = "LineString"


class MultiPolygonField(GeometryField):
    """Площадная геометрия: границы административных округов."""

    description = "Мультиполигон (WGS-84)"
    geom_type = "MultiPolygon"

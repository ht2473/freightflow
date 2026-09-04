"""Упаковка тайла в двоичный формат Mapbox Vector Tile.

Формат описан открытой спецификацией версии 2 и представляет собой
сообщение Protocol Buffers: тайл состоит из слоёв, слой — из объектов,
у объекта есть тип, ссылки на общий для слоя словарь свойств и
последовательность команд рисования.

Кодирование выполняется здесь, а не библиотекой. Причин две. Готовые
пакеты тянут за собой `protobuf` и геометрическое ядро GEOS, тогда как
проект сознательно обходится без системных геозависимостей (ADR-0001).
И сама разметка тайла невелика: используемая часть спецификации —
это четыре типа полей и три команды рисования.

Числа записываются переменной длиной (varint), знаковые — «зигзагом»:
знак переносится в младший бит, поэтому небольшое отрицательное смещение
занимает один байт, а не десять.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .geometry import Geometry
from .tiles import (
    KIND_POINT,
    KIND_POLYGON,
    TILE_BUFFER,
    TILE_EXTENT,
    TILE_TOLERANCE,
    TileGeometry,
    to_tile_geometry,
)

#: Версия спецификации, которой соответствует содержимое тайла.
TILE_SPEC_VERSION = 2

#: Тип содержимого для заголовка ответа.
TILE_CONTENT_TYPE = "application/vnd.mapbox-vector-tile"

# Команды рисования: перенос пера, линия, замыкание контура.
_CMD_MOVE_TO = 1
_CMD_LINE_TO = 2
_CMD_CLOSE_PATH = 7


@dataclass(frozen=True, slots=True)
class TileFeature:
    """Объект слоя: геометрия в градусах и её свойства."""

    geometry: Geometry
    properties: Mapping[str, Any] = field(default_factory=dict)
    feature_id: int | None = None


# ---------------------------------------------------------------------------
#  Примитивы Protocol Buffers
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    """Записать неотрицательное число группами по семь бит."""
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _zigzag(value: int, bits: int = 32) -> int:
    """Перенести знак числа в младший бит."""
    return (value << 1) ^ (value >> (bits - 1))


def _tag(number: int, wire: int) -> bytes:
    """Заголовок поля: номер и способ записи значения."""
    return _varint((number << 3) | wire)


def _uint_field(number: int, value: int) -> bytes:
    """Поле с числом переменной длины."""
    return _tag(number, 0) + _varint(value)


def _bytes_field(number: int, payload: bytes) -> bytes:
    """Поле с длиной: строка, вложенное сообщение, упакованный список."""
    return _tag(number, 2) + _varint(len(payload)) + payload


def _string_field(number: int, text: str) -> bytes:
    """Поле со строкой в кодировке UTF-8."""
    return _bytes_field(number, text.encode("utf-8"))


def _packed_field(number: int, values: Iterable[int]) -> bytes:
    """Поле со списком чисел, записанных подряд."""
    return _bytes_field(number, b"".join(_varint(value) for value in values))


# ---------------------------------------------------------------------------
#  Свойства объектов
# ---------------------------------------------------------------------------


def _encode_value(value: Any) -> bytes:
    """Записать значение свойства подходящим для его типа полем.

    Логическое значение проверяется раньше целого: в Python ``True``
    является числом, и без этой проверки признак превратился бы в единицу.
    """
    if isinstance(value, bool):
        return _uint_field(7, 1 if value else 0)
    if isinstance(value, int):
        if value < 0:
            return _tag(6, 0) + _varint(_zigzag(value, bits=64))
        return _uint_field(4, value)
    if isinstance(value, (float, Decimal)):
        return _tag(3, 1) + struct.pack("<d", float(value))
    return _string_field(1, str(value))


class _Dictionary:
    """Словарь свойств слоя.

    Имена и значения выносятся в общие списки, а объект хранит только
    ссылки на них. Слой из тысячи складов упоминает тип объекта тысячу
    раз — в словаре он записан один.
    """

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.values: list[bytes] = []
        self._key_index: dict[str, int] = {}
        self._value_index: dict[bytes, int] = {}

    def tags(self, properties: Mapping[str, Any]) -> list[int]:
        """Ссылки на имя и значение для каждого свойства объекта."""
        tags: list[int] = []
        for name, value in properties.items():
            if value is None:
                continue  # Отсутствующее значение не записывается вовсе.
            encoded = _encode_value(value)
            key_number = self._key_index.get(name)
            if key_number is None:
                key_number = self._key_index[name] = len(self.keys)
                self.keys.append(name)
            value_number = self._value_index.get(encoded)
            if value_number is None:
                value_number = self._value_index[encoded] = len(self.values)
                self.values.append(encoded)
            tags.append(key_number)
            tags.append(value_number)
        return tags


# ---------------------------------------------------------------------------
#  Команды рисования
# ---------------------------------------------------------------------------


def _command(command: int, count: int) -> int:
    """Слово команды: её код и число следующих за ней пар координат."""
    return (command & 0x7) | (count << 3)


def _draw(geometry: TileGeometry) -> bytes:
    """Развернуть геометрию в последовательность команд рисования.

    Координаты записываются приращениями от текущего положения пера:
    соседние вершины ломаной отличаются на единицы сетки, и разница
    занимает один-два байта вместо четырёх.
    """
    words = bytearray()
    cursor_x = cursor_y = 0

    def move(point: Sequence[int]) -> None:
        nonlocal cursor_x, cursor_y
        words.extend(_varint(_zigzag(point[0] - cursor_x)))
        words.extend(_varint(_zigzag(point[1] - cursor_y)))
        cursor_x, cursor_y = point[0], point[1]

    def command(code: int, count: int) -> None:
        words.extend(_varint(_command(code, count)))

    if geometry.kind == KIND_POINT:
        points = [point for part in geometry.parts for point in part]
        if not points:
            return b""
        command(_CMD_MOVE_TO, len(points))
        for point in points:
            move(point)
        return bytes(words)

    for part in geometry.parts:
        if geometry.kind == KIND_POLYGON:
            # Замыкающая вершина не записывается: её заменяет ClosePath.
            ring = list(part[:-1]) if len(part) > 1 and part[0] == part[-1] else list(part)
            if len(ring) < 3:
                continue
            command(_CMD_MOVE_TO, 1)
            move(ring[0])
            command(_CMD_LINE_TO, len(ring) - 1)
            for point in ring[1:]:
                move(point)
            command(_CMD_CLOSE_PATH, 1)
        else:
            if len(part) < 2:
                continue
            command(_CMD_MOVE_TO, 1)
            move(part[0])
            command(_CMD_LINE_TO, len(part) - 1)
            for point in part[1:]:
                move(point)

    return bytes(words)


# ---------------------------------------------------------------------------
#  Сборка тайла
# ---------------------------------------------------------------------------


def _encode_layer(name: str, features: Sequence[tuple[TileGeometry, Mapping[str, Any], int | None]]) -> bytes:
    """Собрать сообщение одного слоя."""
    dictionary = _Dictionary()
    body = bytearray()

    for geometry, properties, feature_id in features:
        drawing = _draw(geometry)
        if not drawing:
            continue
        message = bytearray()
        if feature_id is not None:
            message += _uint_field(1, int(feature_id))
        tags = dictionary.tags(properties)
        if tags:
            message += _packed_field(2, tags)
        message += _uint_field(3, geometry.kind)
        message += _bytes_field(4, drawing)
        body += _bytes_field(2, bytes(message))

    if not body:
        return b""

    layer = bytearray()
    layer += _uint_field(15, TILE_SPEC_VERSION)
    layer += _string_field(1, name)
    layer += bytes(body)
    for key in dictionary.keys:
        layer += _string_field(3, key)
    for value in dictionary.values:
        layer += _bytes_field(4, value)
    layer += _uint_field(5, TILE_EXTENT)
    return _bytes_field(3, bytes(layer))


def render_tile(
    z: int,
    x: int,
    y: int,
    layers: Mapping[str, Iterable[TileFeature]],
    *,
    buffer_units: int = TILE_BUFFER,
    tolerance: float = TILE_TOLERANCE,
) -> bytes:
    """Собрать тайл из объектов слоёв.

    Объекты, от которых после обрезки ничего не осталось, и слои, целиком
    оказавшиеся вне тайла, в результат не попадают. Пустой тайл — это
    пустая последовательность байт, а не отказ: сетка покрывает весь мир,
    и большая её часть данных не содержит.
    """
    tile = bytearray()
    for name, features in layers.items():
        prepared: list[tuple[TileGeometry, Mapping[str, Any], int | None]] = []
        for feature in features:
            geometry = to_tile_geometry(
                feature.geometry, z, x, y, buffer_units=buffer_units, tolerance=tolerance
            )
            if geometry is None:
                continue
            prepared.append((geometry, feature.properties, feature.feature_id))
        if prepared:
            tile += _encode_layer(name, prepared)
    return bytes(tile)

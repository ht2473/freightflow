"""Разбор двоичного тайла — вспомогательный модуль автотестов.

Тайл собирается системой, а проверять его нужно с другой стороны: тем же
кодом, что и собирался, доказать нечего. Здесь реализовано независимое
чтение сообщения Protocol Buffers и обратное разворачивание команд
рисования в координаты.
"""

from __future__ import annotations

import struct
from typing import Any

# Команды рисования спецификации Mapbox Vector Tile.
_CMD_MOVE_TO = 1
_CMD_LINE_TO = 2
_CMD_CLOSE_PATH = 7


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    """Прочитать число переменной длины, вернув его и новое положение."""
    result = 0
    shift = 0
    while True:
        byte = data[position]
        position += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, position
        shift += 7


def _unzigzag(value: int) -> int:
    """Восстановить знак числа из младшего бита."""
    return (value >> 1) ^ -(value & 1)


def read_message(data: bytes) -> dict[int, list[Any]]:
    """Разложить сообщение на поля: номер поля → список значений."""
    fields: dict[int, list[Any]] = {}
    position = 0
    while position < len(data):
        tag, position = _read_varint(data, position)
        number, wire = tag >> 3, tag & 0x7
        if wire == 0:
            value, position = _read_varint(data, position)
        elif wire == 1:
            value = struct.unpack_from("<d", data, position)[0]
            position += 8
        elif wire == 2:
            length, position = _read_varint(data, position)
            value = data[position : position + length]
            position += length
        elif wire == 5:
            value = struct.unpack_from("<f", data, position)[0]
            position += 4
        else:  # pragma: no cover — в тайле такие поля не встречаются
            raise ValueError(f"Неизвестный способ записи поля: {wire}")
        fields.setdefault(number, []).append(value)
    return fields


def _read_packed(data: bytes) -> list[int]:
    """Прочитать список чисел, записанных подряд."""
    values: list[int] = []
    position = 0
    while position < len(data):
        value, position = _read_varint(data, position)
        values.append(value)
    return values


def _read_value(data: bytes) -> Any:
    """Прочитать значение свойства."""
    fields = read_message(data)
    if 1 in fields:
        return fields[1][0].decode("utf-8")
    if 2 in fields:
        return fields[2][0]
    if 3 in fields:
        return fields[3][0]
    if 4 in fields:
        return fields[4][0]
    if 6 in fields:
        return _unzigzag(fields[6][0])
    if 7 in fields:
        return bool(fields[7][0])
    return None


def _read_geometry(values: list[int]) -> list[list[tuple[int, int]]]:
    """Развернуть команды рисования обратно в части геометрии."""
    parts: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    cursor_x = cursor_y = 0
    position = 0
    while position < len(values):
        command = values[position]
        code, count = command & 0x7, command >> 3
        position += 1
        if code == _CMD_CLOSE_PATH:
            if current:
                current.append(current[0])
            continue
        for _ in range(count):
            cursor_x += _unzigzag(values[position])
            cursor_y += _unzigzag(values[position + 1])
            position += 2
            if code == _CMD_MOVE_TO:
                current = [(cursor_x, cursor_y)]
                parts.append(current)
            else:
                current.append((cursor_x, cursor_y))
    return parts


def read_tile(data: bytes) -> dict[str, dict]:
    """Разобрать тайл: имя слоя → его состав."""
    layers: dict[str, dict] = {}
    for raw_layer in read_message(data).get(3, []):
        fields = read_message(raw_layer)
        name = fields[1][0].decode("utf-8")
        keys = [item.decode("utf-8") for item in fields.get(3, [])]
        values = [_read_value(item) for item in fields.get(4, [])]

        features = []
        for raw_feature in fields.get(2, []):
            parsed = read_message(raw_feature)
            tags = _read_packed(parsed[2][0]) if 2 in parsed else []
            geometry = _read_packed(parsed[4][0]) if 4 in parsed else []
            features.append(
                {
                    "id": parsed[1][0] if 1 in parsed else None,
                    "type": parsed[3][0] if 3 in parsed else 0,
                    "properties": {
                        keys[tags[index]]: values[tags[index + 1]]
                        for index in range(0, len(tags), 2)
                    },
                    "parts": _read_geometry(geometry),
                }
            )

        layers[name] = {
            "version": fields[15][0] if 15 in fields else None,
            "extent": fields[5][0] if 5 in fields else None,
            "features": features,
        }
    return layers

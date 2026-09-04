"""Проверки качества записей на входе в систему.

Данные из внешних источников неоднородны: в разметке сообщества встречаются
объекты без координат, в статистических таблицах — примечания вместо чисел,
в присланных файлах — строки с пропущенными обязательными полями. Записывать
такое в реестр нельзя, а молча отбрасывать — тем более: доля отклонений
и её причины сами являются показателем качества источника.

Проверка описывается объектом :class:`Check`: код, человеческая формулировка
и условие. Условие получает кандидата целиком, поэтому проверять можно и
приведённые значения, и сведения об исходной записи. Набор проверок объявляется
конвейером и виден в его описании — по нему читается, какие требования
предъявляются к источнику.

Записи, не прошедшие проверку, попадают в карантин вместе с кодом проверки,
формулировкой и исходным содержимым.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


class Inspectable(Protocol):
    """Объект, к которому применяется проверка.

    Кандидат конвейера удовлетворяет этому протоколу: ``values`` содержит
    приведённые значения полей, ``extra`` — сведения об исходной записи,
    нужные проверкам, но не попадающие в базу.
    """

    values: dict[str, Any]
    extra: dict[str, Any]


@dataclass(frozen=True)
class Violation:
    """Нарушение, выявленное проверкой."""

    code: str
    message: str


@dataclass(frozen=True)
class Check:
    """Проверка качества записи.

    Атрибуты:
        code: обозначение проверки, попадающее в карантин и в сводку;
        title: формулировка требования — то, что должно выполняться;
        test: условие; возвращает описание нарушения либо ``None``.
    """

    code: str
    title: str
    test: Callable[[Any], str | None]

    def inspect(self, candidate: Inspectable) -> Violation | None:
        """Применить проверку к кандидату."""
        message = self.test(candidate)
        return Violation(self.code, message) if message else None


def _field(candidate: Inspectable, name: str) -> Any:
    """Значение поля кандидата: сначала приведённое, затем исходное."""
    if name in candidate.values:
        return candidate.values[name]
    return candidate.extra.get(name)


# ---------------------------------------------------------------------------
#  Готовые проверки
# ---------------------------------------------------------------------------


def required(field: str, label: str) -> Check:
    """Поле обязано быть заполненным.

    Пустой строкой и отсутствием значения проверка не различает: и то и другое
    означает, что сведения нет.
    """

    def test(candidate: Inspectable) -> str | None:
        value = _field(candidate, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"{label} не указано"
        return None

    return Check(f"required.{field}", f"{label} заполнено", test)


def not_negative(field: str, label: str) -> Check:
    """Величина неотрицательна.

    Отрицательный объём или площадь означают ошибку разбора исходного
    значения, а не свойство объекта.
    """

    def test(candidate: Inspectable) -> str | None:
        value = _field(candidate, field)
        if value is None:
            return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return f"{label}: значение «{value}» не является числом"
        if number < 0:
            return f"{label} отрицательно: {value}"
        return None

    return Check(f"positive.{field}", f"{label} неотрицательно", test)


def within(field: str, low: float, high: float, label: str, unit: str = "") -> Check:
    """Величина лежит в допустимых пределах.

    Границы задаются по предметной области, а не по типу колонки: значение
    вне их означает ошибку в источнике, и записывать его нельзя даже тогда,
    когда база такое значение примет.
    """
    measure = f" {unit}" if unit else ""

    def test(candidate: Inspectable) -> str | None:
        value = _field(candidate, field)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return f"{label}: значение «{value}» не является числом"
        if not low <= number <= high:
            return (
                f"{label} вне допустимых пределов: {number:g}{measure} "
                f"при допустимых от {low:g} до {high:g}{measure}"
            )
        return None

    return Check(f"range.{field}", f"{label} в пределах {low:g}–{high:g}{measure}", test)


def one_of(field: str, allowed: Collection[str], label: str) -> Check:
    """Значение принадлежит перечислению."""
    permitted = set(allowed)

    def test(candidate: Inspectable) -> str | None:
        value = _field(candidate, field)
        if value in (None, ""):
            return None
        if str(value) not in permitted:
            return f"{label}: недопустимое значение «{value}»"
        return None

    return Check(f"choice.{field}", f"{label} из числа допустимых", test)


def fits(field: str, length: int, label: str) -> Check:
    """Значение помещается в отведённое поле.

    Наименования из внешних источников бывают длиннее колонки. Обрезать их
    молча нельзя: усечённое наименование неотличимо от настоящего.
    """

    def test(candidate: Inspectable) -> str | None:
        value = _field(candidate, field)
        if isinstance(value, str) and len(value) > length:
            return f"{label} длиннее {length} символов: {len(value)}"
        return None

    return Check(f"length.{field}", f"{label} не длиннее {length} символов", test)


def condition(code: str, title: str, test: Callable[[Any], str | None]) -> Check:
    """Проверка, выражаемая только особым условием предметной области."""
    return Check(code, title, test)


__all__ = [
    "Check",
    "Violation",
    "condition",
    "fits",
    "not_negative",
    "one_of",
    "required",
    "within",
]

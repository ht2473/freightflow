"""Теги и фильтры шаблонов ИС «ГрузПоток».

Набор намеренно узкий: в него включены только те преобразования, которые
иначе пришлось бы дублировать в представлениях или, что хуже, выражать
громоздкими цепочками стандартных фильтров.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

register = template.Library()


@register.simple_tag(takes_context=True)
def query(context, **overrides) -> str:
    """Сформировать строку запроса на основе текущей, заменив параметры.

    Используется в ссылках сортировки и постраничной навигации: наложенные
    пользователем условия отбора при переходе не теряются.

    Пример::

        <a href="{% query sort='capacity' page=None %}">По мощности</a>
    """
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in overrides.items():
        if value in (None, "", False):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter
def tons(value) -> str:
    """Отформатировать массу с автоматическим выбором единицы измерения.

    Тысячи тонн и миллионы тонн читаются заметно легче, чем длинные ряды
    цифр, поэтому единица подбирается по порядку величины.
    """
    number = _to_float(value)
    if number is None:
        return "—"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:,.2f} млн т".replace(",", " ")
    if abs(number) >= 1_000:
        return f"{number / 1_000:,.1f} тыс. т".replace(",", " ")
    return f"{number:,.0f} т".replace(",", " ")


@register.filter
def spaced(value, precision: int = 0) -> str:
    """Разделить разряды числа неразрывными пробелами."""
    number = _to_float(value)
    if number is None:
        return "—"
    formatted = f"{number:,.{int(precision)}f}".replace(",", "\u202f")
    return formatted.replace(".", ",")


@register.filter
def dash(value) -> str:
    """Заменить пустое значение типографским тире."""
    if value in (None, "", []):
        return "—"
    return value


@register.filter
def percent(value, precision: int = 1) -> str:
    """Отформатировать долю в процентах."""
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number:.{int(precision)}f} %".replace(".", ",")


@register.filter
def ratio(value, total) -> float:
    """Вычислить долю значения от суммы в процентах.

    Применяется для ширины полос в рейтингах; при нулевой сумме возвращает
    ноль, что исключает деление на ноль в шаблоне.
    """
    numerator = _to_float(value)
    denominator = _to_float(total)
    if not numerator or not denominator:
        return 0.0
    return min(numerator / denominator * 100, 100.0)


@register.filter
def tone(value) -> str:
    """Определить модификатор оформления по баллу загруженности 0–10."""
    number = _to_float(value)
    if number is None:
        return "muted"
    if number >= 9:
        return "crit"
    if number >= 7:
        return "alert"
    if number >= 5:
        return "warn"
    return "ok"


@register.filter
def severity_tone(value) -> str:
    """Определить модификатор оформления по уровню серьёзности 1–5."""
    number = _to_float(value)
    if number is None:
        return "muted"
    if number >= 5:
        return "crit"
    if number >= 4:
        return "alert"
    if number >= 2:
        return "warn"
    return "ok"


@register.inclusion_tag("partials/_origin.html")
def origin(value: str) -> dict:
    """Отметка происхождения величины: измерена, рассчитана или получена моделью.

    Отметка ставится у самого числа, а не в пояснении к нему: читатель
    принимает решение, глядя на величину, и должен видеть здесь же, чем
    она подкреплена. Начертание отметки различает три случая линией
    подчёркивания — сплошной, штриховой и точечной, — поэтому признак
    сохраняется на монохромной печати и не расходует цвет, отданный
    шкале состояний.
    """
    from core.choices import DataOrigin

    try:
        kind = DataOrigin(value)
    except ValueError:
        return {"code": "", "label": "", "meaning": ""}
    meanings = {
        DataOrigin.MEASURED: gettext(
            "Значение получено из источника без преобразований"
        ),
        DataOrigin.DERIVED: gettext(
            "Значение выведено расчётом из измеренных величин"
        ),
        DataOrigin.MODELLED: gettext(
            "Значение получено имитационной моделью: наблюдений не существует"
        ),
    }
    return {"code": kind.value, "label": kind.label, "meaning": meanings[kind]}


@register.filter
def field_class(field, css: str = "") -> str:
    """Добавить класс к виджету поля формы прямо в шаблоне."""
    existing = field.field.widget.attrs.get("class", "")
    return field.as_widget(attrs={"class": f"{existing} {css}".strip()})


@register.simple_tag
def sort_link(context_request, code: str, current: str, title: str) -> str:
    """Построить заголовок колонки со ссылкой сортировки.

    Повторное нажатие по активной колонке меняет направление на обратное.
    """
    active = current == code
    target = f"-{code}" if active and not current.startswith("-") else code
    params = context_request.GET.copy()
    params["sort"] = target
    params.pop("page", None)
    marker = " ↓" if active and not current.startswith("-") else (" ↑" if active else "")
    return mark_safe(
        f'<a href="?{params.urlencode()}">{title}<span aria-hidden="true">{marker}</span></a>'
    )


def _to_float(value) -> float | None:
    """Привести значение к вещественному числу, вернув ``None`` при неудаче."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError, InvalidOperation):
        return None


@register.simple_tag
def elided_range(page_obj, on_each_side: int = 2, on_ends: int = 1):
    """Диапазон номеров страниц с многоточиями вокруг текущей страницы.

    Стандартный метод пагинатора требует передачи номера текущей страницы,
    что невозможно сделать напрямую из шаблона, — тег закрывает этот пробел.
    """
    return page_obj.paginator.get_elided_page_range(
        number=page_obj.number, on_each_side=on_each_side, on_ends=on_ends
    )


@register.simple_tag
def chart_from(rows, label_key: str, value_key: str, date_format: str = "%m.%y",
               title: str = "") -> str:
    """Собрать описание графика из последовательности словарей или объектов.

    Тег избавляет представления от необходимости готовить отдельную структуру
    под каждый график: данные, уже собранные для таблицы, переиспользуются
    для построения изображения.
    """
    import json
    from datetime import date, datetime

    labels: list[str] = []
    values: list[float] = []

    for row in rows or []:
        label = row.get(label_key) if isinstance(row, dict) else getattr(row, label_key, None)
        value = row.get(value_key) if isinstance(row, dict) else getattr(row, value_key, None)
        if isinstance(label, (date, datetime)):
            label = label.strftime(date_format)
        labels.append(str(label))
        values.append(_to_float(value) or 0.0)

    return json.dumps(
        {"title": title, "labels": labels, "series": [{"values": values}]}, ensure_ascii=False
    )


@register.simple_tag
def chart_tones(rows, value_key: str, scale: str = "congestion") -> str:
    """Список модификаторов оформления столбцов по семафорной шкале."""
    import json

    picker = tone if scale == "congestion" else severity_tone
    values = [
        picker(row.get(value_key) if isinstance(row, dict) else getattr(row, value_key, None))
        for row in rows or []
    ]
    return json.dumps(values, ensure_ascii=False)


@register.filter
def plural(value, forms: str) -> str:
    """Форма существительного при числе: «1 округ», «2 округа», «5 округов».

    Встроенный ``pluralize`` различает две формы и рассчитан на языки,
    где их две. Формы передаются через запятую в порядке «один — два — пять».
    """
    one, few, many = (part.strip() for part in forms.split(","))
    number = _to_float(value)
    if number is None:
        return many
    count = abs(int(number))
    if count % 100 in range(11, 15):
        return many
    remainder = count % 10
    if remainder == 1:
        return one
    if remainder in (2, 3, 4):
        return few
    return many


@register.filter
def lookup(mapping, key):
    """Значение по ключу, известному только во время вывода.

    Состав аналитических показателей задан реестром, а не разметкой: таблица
    перебирает реестр, и обращаться к колонке приходится по её обозначению.
    """
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def divide(value, divisor):
    """Частное двух величин; при нулевом делителе возвращает ``None``."""
    numerator = _to_float(value)
    denominator = _to_float(divisor)
    if numerator is None or not denominator:
        return None
    return numerator / denominator

@register.filter
def translated(value) -> str:
    """Перевести строку, сохранённую в базе данных.

    Подписи действий в журнале аудита записываются на русском языке — он же
    служит ключом перевода. Переводить их при записи нельзя: значение
    сохранилось бы на языке, выбранном в момент действия, и не менялось бы
    при переключении интерфейса. Поэтому перевод выполняется при выводе.

    Для строк, отсутствующих в словаре, возвращается исходное значение — так
    ведёт себя механизм перевода Django по умолчанию.
    """
    return gettext(str(value)) if value else ""

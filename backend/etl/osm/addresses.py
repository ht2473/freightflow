"""Сборка почтового адреса из тегов OpenStreetMap.

Адрес в OpenStreetMap разложен по отдельным тегам семейства ``addr:*``,
причём заполнены они неравномерно: у одного объекта есть улица и дом,
у другого только улица, у третьего вместо улицы указано место.

Модуль собирает из них строку в порядке, принятом в российской адресации:
город, улица, дом. Отсутствующие части пропускаются, а не подменяются
заполнителем: неполный адрес честнее выдуманного, и по нему видно,
какие сведения ещё предстоит уточнить.
"""

from __future__ import annotations

#: Город по умолчанию: выгрузка ограничена границами Москвы, и повторять
#: это в каждом адресе не нужно. Указывается, только если в разметке стоит
#: другой населённый пункт — на присоединённых территориях это обычное дело.
DEFAULT_CITY = "Москва"


def build_address(tags: dict[str, str]) -> str:
    """Собрать адрес объекта из тегов ``addr:*``.

    Возвращает пустую строку, если ни одной части адреса не размечено.
    """
    parts: list[str] = []

    city = (tags.get("addr:city") or "").strip()
    if city and city != DEFAULT_CITY:
        parts.append(city)

    street = (tags.get("addr:street") or tags.get("addr:place") or "").strip()
    if street:
        parts.append(street)

    house = (tags.get("addr:housenumber") or "").strip()
    if house:
        # Номер дома без улицы бессмыслен: без привязки к улице он не
        # определяет положения и только загромождает карточку.
        parts.append(f"д. {house}" if street else "")

    return ", ".join(part for part in parts if part)


def build_contacts(tags: dict[str, str]) -> tuple[str, str]:
    """Извлечь сайт и телефон.

    Контакты размечаются двумя равноправными наборами тегов — с префиксом
    ``contact:`` и без него. Предпочтение отдаётся первому: он новее
    и в московских данных заполнен чаще.
    """
    website = (
        tags.get("contact:website")
        or tags.get("website")
        or tags.get("url")
        or ""
    ).strip()

    phone = (tags.get("contact:phone") or tags.get("phone") or "").strip()

    return website[:300], phone[:64]


def build_operator(tags: dict[str, str]) -> str:
    """Определить эксплуатирующую организацию.

    Тег ``operator`` заполнен не всегда; при его отсутствии подходит
    ``brand`` — у сетевых складских операторов название сети и есть
    название эксплуатанта.
    """
    return (tags.get("operator") or tags.get("brand") or "").strip()[:200]


def build_opening_hours(tags: dict[str, str]) -> str:
    """Привести режим работы к виду, принятому в реестре.

    Круглосуточный режим в OpenStreetMap записывается как ``24/7``;
    в реестре он обозначается словом, понятным читателю без знания
    условных обозначений.
    """
    hours = (tags.get("opening_hours") or "").strip()
    if hours == "24/7":
        return "круглосуточно"
    return hours[:64]

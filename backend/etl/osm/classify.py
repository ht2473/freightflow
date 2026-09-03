"""Отнесение объектов OpenStreetMap к типам логистической инфраструктуры.

Данные OpenStreetMap размечены сообществом и по составу тегов заметно шире
предметной области: под ``landuse=industrial`` попадают теплоэлектроцентрали,
водопроводные узлы и научные институты, под ``industrial=depot`` — трамвайные
депо и подстанции скорой помощи, под ``shop=storage_rental`` — потребительские
боксы самохранения. Прямая выгрузка по этим тегам дала бы реестр, в котором
логистики меньше половины.

Поэтому отбор двухступенчатый.

**Первая ступень — включение.** Объект попадает в реестр, если хотя бы один
его признак однозначно указывает на грузовую логистику: складское здание,
контейнерный терминал, грузовой двор, стоянка грузового транспорта, пункт
весового контроля. Признаки перечислены в :data:`INCLUSION_RULES`.

**Вторая ступень — исключение.** Объект отбрасывается, если он подходит под
правило исключения: потребительское самохранение, пассажирский транспорт,
коммунальное и энергетическое хозяйство. Правила перечислены
в :data:`EXCLUSION_RULES`.

Каждое решение сопровождается ссылкой на сработавшее правило, поэтому состав
реестра прослеживается до конкретного признака исходных данных, а спорные
случаи разбираются, а не теряются.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
#  Коды типов, совпадающие со справочником infrastructure_types
# ---------------------------------------------------------------------------

WAREHOUSE = "warehouse"
TERMINAL = "terminal"
CARGO_YARD = "cargo_yard"
PARKING = "parking"
CHECKPOINT = "checkpoint"
DISTRIBUTION = "distribution"
TRUCKING = "trucking"


@dataclass(frozen=True)
class Verdict:
    """Результат отнесения объекта к типу.

    Атрибуты:
        type_code: код типа из справочника либо ``None``, если объект отклонён;
        rule: обозначение сработавшего правила — попадает в журнал загрузки
            и в карточку объекта, чтобы состав реестра можно было проверить;
        reason: пояснение для человека.
    """

    type_code: str | None
    rule: str
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.type_code is not None


@dataclass(frozen=True)
class Rule:
    """Правило отбора.

    Атрибуты:
        code: обозначение правила, попадающее в журнал;
        type_code: тип, присваиваемый объекту (для правил включения);
        tags: признаки вида «ключ → допустимые значения»; пустое множество
            значений означает «любое значение тега»;
        name_pattern: выражение, применяемое к наименованию объекта;
        requires_name: правило применимо только к именованным объектам.
    """

    code: str
    type_code: str | None
    tags: dict[str, frozenset[str]] = field(default_factory=dict)
    name_pattern: re.Pattern | None = None
    requires_name: bool = False
    reason: str = ""

    def matches(self, tags: dict[str, str]) -> bool:
        name = (tags.get("name") or "").strip()
        if self.requires_name and not name:
            return False

        for key, values in self.tags.items():
            actual = tags.get(key)
            if actual is None:
                return False
            if values and actual not in values:
                return False

        if self.name_pattern is not None and not self.name_pattern.search(name.lower()):
            return False

        return bool(self.tags) or self.name_pattern is not None


def _tag(key: str, *values: str) -> dict[str, frozenset[str]]:
    return {key: frozenset(values)}


def _words(*words: str) -> re.Pattern:
    """Выражение, находящее любое из слов в наименовании."""
    return re.compile("|".join(words))


# ---------------------------------------------------------------------------
#  Правила исключения. Применяются первыми.
# ---------------------------------------------------------------------------

#: Наименования, указывающие на пассажирский транспорт и городское хозяйство.
#: Эти объекты размечены теми же тегами, что и грузовые предприятия, и
#: отделяются только по наименованию.
_NON_FREIGHT_NAMES = _words(
    r"трамвайн\w* депо", r"троллейбусн\w* парк", r"автобусн\w* парк",
    r"электробусн\w* парк", r"метродепо", r"депо метро",
    r"скор\w* помощ", r"жилищник", r"мосводоканал", r"водопроводн",
    r"теплов\w* станц", r"тэц", r"котельн", r"подстанц",
    r"снегоплавильн", r"мусоросжига", r"очистн\w* сооружен",
    r"пожарн\w* част", r"библиотек", r"поликлиник", r"больниц", r"школ",
)

EXCLUSION_RULES: tuple[Rule, ...] = (
    Rule(
        code="X-SELF-STORAGE",
        type_code=None,
        tags=_tag("shop", "storage_rental"),
        reason="потребительское самохранение, не грузовая инфраструктура",
    ),
    Rule(
        code="X-PASSENGER-DEPOT",
        type_code=None,
        tags=_tag("depot"),
        reason="депо пассажирского транспорта",
    ),
    Rule(
        code="X-UTILITY",
        type_code=None,
        tags=_tag("industrial", "snow_melting", "heating_station", "wastewater",
                  "water_works", "power"),
        reason="объект коммунального или энергетического хозяйства",
    ),
    Rule(
        code="X-NON-FREIGHT-NAME",
        type_code=None,
        name_pattern=_NON_FREIGHT_NAMES,
        requires_name=True,
        reason="наименование указывает на непрофильное назначение",
    ),
)


# ---------------------------------------------------------------------------
#  Правила включения. Порядок значим: первое сработавшее определяет тип.
# ---------------------------------------------------------------------------

INCLUSION_RULES: tuple[Rule, ...] = (
    # --- Терминалы -----------------------------------------------------------
    Rule(
        code="I-CONTAINER-TERMINAL",
        type_code=TERMINAL,
        tags=_tag("industrial", "container_terminal"),
        reason="контейнерный терминал по разметке OSM",
    ),
    Rule(
        code="I-TERMINAL-NAME",
        type_code=TERMINAL,
        name_pattern=_words(r"терминал", r"\bтлц\b", r"контейнерн\w* площадк"),
        requires_name=True,
        reason="наименование указывает на грузовой терминал",
    ),
    # --- Грузовые дворы ------------------------------------------------------
    Rule(
        code="I-RAILWAY-YARD",
        type_code=CARGO_YARD,
        tags=_tag("railway", "yard"),
        reason="железнодорожный грузовой двор",
    ),
    Rule(
        code="I-CARGO-YARD-NAME",
        type_code=CARGO_YARD,
        tags=_tag("landuse", "railway"),
        name_pattern=_words(r"грузов", r"товарн", r"контейнерн"),
        requires_name=True,
        reason="грузовой двор железнодорожной станции",
    ),
    # --- Распределительные центры -------------------------------------------
    Rule(
        code="I-DISTRIBUTION-NAME",
        type_code=DISTRIBUTION,
        name_pattern=_words(
            r"распределительн\w* центр", r"\bрц\b", r"фулфилмент", r"fulfillment",
            r"сортировочн\w* центр", r"логистическ\w* центр", r"логопарк",
        ),
        requires_name=True,
        reason="наименование указывает на распределительный или сортировочный центр",
    ),
    # --- Стоянки и контроль --------------------------------------------------
    Rule(
        code="I-HGV-PARKING",
        type_code=PARKING,
        tags={"amenity": frozenset({"parking"}), "hgv": frozenset({"yes", "designated"})},
        reason="стоянка, открытая для грузового транспорта",
    ),
    Rule(
        code="I-TRUCK-PARKING",
        type_code=PARKING,
        tags=_tag("parking", "truck"),
        reason="стоянка грузового транспорта",
    ),
    Rule(
        code="I-WEIGHBRIDGE",
        type_code=CHECKPOINT,
        tags=_tag("amenity", "weighbridge"),
        reason="пункт весового контроля",
    ),
    # --- Автотранспортные предприятия ---------------------------------------
    # Грузовые автопредприятия размечены тем же industrial=depot, что и депо
    # пассажирского транспорта, и отделяются только по наименованию. Правила
    # исключения к этому моменту уже отсеяли непрофильные объекты.
    Rule(
        code="I-TRUCKING-NAME",
        type_code=TRUCKING,
        tags=_tag("industrial", "depot"),
        name_pattern=_words(
            r"автокомбинат", r"автобаза", r"автотранспортн", r"грузов",
            r"автоколонн", r"транспортн\w* компан", r"транспортн\w* комбинат",
            r"экспедиц", r"перевозк",
        ),
        requires_name=True,
        reason="автотранспортное предприятие грузовых перевозок",
    ),
    # --- Склады --------------------------------------------------------------
    Rule(
        code="I-WAREHOUSE-BUILDING",
        type_code=WAREHOUSE,
        tags=_tag("building", "warehouse"),
        reason="складское здание по разметке OSM",
    ),
    Rule(
        code="I-WAREHOUSE-INDUSTRIAL",
        type_code=WAREHOUSE,
        tags=_tag("industrial", "warehouse"),
        reason="складская территория по разметке OSM",
    ),
    Rule(
        code="I-LOGISTICS-LANDUSE",
        type_code=WAREHOUSE,
        tags=_tag("landuse", "logistics"),
        reason="территория логистического назначения",
    ),
    Rule(
        code="I-WAREHOUSE-NAME",
        type_code=WAREHOUSE,
        name_pattern=_words(r"склад", r"складск", r"грузов\w* баз"),
        requires_name=True,
        reason="наименование указывает на складской объект",
    ),
)


# ---------------------------------------------------------------------------
#  Отнесение
# ---------------------------------------------------------------------------

REJECTED_UNMATCHED = Verdict(
    None, "X-NO-RULE", "ни одно правило включения не применимо"
)


def classify(tags: dict[str, str]) -> Verdict:
    """Определить тип объекта по его тегам.

    Возвращает :class:`Verdict`; у отклонённого объекта ``type_code`` равен
    ``None``, а ``rule`` содержит обозначение сработавшего правила исключения
    либо ``X-NO-RULE``.

    Порядок разбора: сначала исключения, затем включения. Обратный порядок
    относил бы, например, боксы самохранения к складам — у них есть и
    ``building=warehouse``, и слово «склад» в наименовании.
    """
    if not tags:
        return REJECTED_UNMATCHED

    for rule in EXCLUSION_RULES:
        if rule.matches(tags):
            return Verdict(None, rule.code, rule.reason)

    for rule in INCLUSION_RULES:
        if rule.matches(tags):
            return Verdict(rule.type_code, rule.code, rule.reason)

    return REJECTED_UNMATCHED


def rule_index() -> dict[str, Rule]:
    """Правила по их обозначению — для пояснений в интерфейсе и отчётах."""
    return {rule.code: rule for rule in (*EXCLUSION_RULES, *INCLUSION_RULES)}

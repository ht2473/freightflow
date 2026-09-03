"""Отнесение объектов OpenStreetMap к типам логистической инфраструктуры.

Метод: модульное тестирование, позитивные и негативные сценарии,
проверка граничных случаев разметки.

Разметка OpenStreetMap ведётся сообществом и по составу тегов шире предметной
области: одни и те же признаки носят и складской комплекс, и трамвайное депо.
Набор закрепляет обе стороны отбора — что включается и что отбрасывается, —
поскольку ошибка в любую сторону искажает состав реестра, а через него
и все расчёты, которые на нём строятся.
"""

from __future__ import annotations

import pytest
from etl.osm import classify as rules
from etl.osm.classify import classify


class TestWarehouses:
    """Складские объекты."""

    @pytest.mark.parametrize(
        "tags,expected_rule",
        [
            ({"building": "warehouse"}, "I-WAREHOUSE-BUILDING"),
            ({"industrial": "warehouse"}, "I-WAREHOUSE-INDUSTRIAL"),
            ({"landuse": "logistics"}, "I-LOGISTICS-LANDUSE"),
            ({"landuse": "industrial", "name": "Складская база №4"}, "I-WAREHOUSE-NAME"),
        ],
    )
    def test_warehouse_signals(self, tags, expected_rule):
        """Каждый признак складского назначения распознаётся."""
        verdict = classify(tags)
        assert verdict.type_code == rules.WAREHOUSE
        assert verdict.rule == expected_rule


class TestTerminals:
    """Грузовые терминалы."""

    def test_container_terminal_tag(self):
        """Контейнерный терминал распознаётся по разметке."""
        verdict = classify({"industrial": "container_terminal", "name": "ТЛЦ Кунцево"})
        assert verdict.type_code == rules.TERMINAL
        assert verdict.rule == "I-CONTAINER-TERMINAL"

    @pytest.mark.parametrize(
        "name",
        [
            "Грузовой терминал компании «Деловые линии»",
            "ТЛЦ Кунцево",
            "Контейнерный терминал Мещерский",
        ],
    )
    def test_terminal_by_name(self, name):
        """Наименование терминала распознаётся при неполной разметке."""
        assert classify({"landuse": "industrial", "name": name}).type_code == rules.TERMINAL

    def test_terminal_wins_over_warehouse(self):
        """Складской терминал относится к терминалам, а не к складам.

        Правила включения разбираются по порядку, и более узкое назначение
        должно определяться раньше. Иначе «складской терминал» попал бы
        в склады по слову «складской».
        """
        verdict = classify({"building": "warehouse", "name": "Складской терминал «Еврологистика»"})
        assert verdict.type_code == rules.TERMINAL


class TestDistribution:
    """Распределительные и сортировочные центры."""

    @pytest.mark.parametrize(
        "name",
        [
            "Логистический центр «1С»",
            "Распределительный центр «Внуково»",
            "Сортировочный центр Почты России",
            "Логопарк «Север»",
            "Фулфилмент-центр Ozon",
        ],
    )
    def test_distribution_by_name(self, name):
        """Наименования распределительных центров распознаются."""
        assert classify({"landuse": "industrial", "name": name}).type_code == rules.DISTRIBUTION


class TestTrucking:
    """Автотранспортные предприятия грузовых перевозок."""

    @pytest.mark.parametrize(
        "name",
        [
            "Автокомбинат №12",
            "АО «Совтрансавтоэкспедиция»",
            "Автобаза №2",
            "Транспортный комбинат «Россия»",
        ],
    )
    def test_freight_depot_accepted(self, name):
        """Грузовое автопредприятие отделяется от депо по наименованию."""
        verdict = classify({"industrial": "depot", "name": name})
        assert verdict.type_code == rules.TRUCKING
        assert verdict.rule == "I-TRUCKING-NAME"

    @pytest.mark.parametrize(
        "name",
        [
            "Октябрьское трамвайное депо",
            "Электробусный парк «Салтыковка»",
            "Подстанция скорой помощи №22",
            "Жилищник района Сокольники",
        ],
    )
    def test_passenger_and_municipal_rejected(self, name):
        """Пассажирский транспорт и городское хозяйство в реестр не попадают.

        Эти объекты размечены тем же industrial=depot, что и грузовые
        автопредприятия, и отделяются только по наименованию.
        """
        assert not classify({"industrial": "depot", "name": name}).accepted


class TestExclusions:
    """Правила исключения."""

    def test_self_storage_rejected(self):
        """Боксы самохранения — не грузовая инфраструктура.

        Их разметка совпадает со складской: есть и building=warehouse,
        и слово «склад» в наименовании. Отделяет только shop=storage_rental,
        поэтому исключения разбираются раньше включений.
        """
        verdict = classify({
            "shop": "storage_rental", "building": "warehouse", "name": "АльфаСклад",
        })
        assert not verdict.accepted
        assert verdict.rule == "X-SELF-STORAGE"

    @pytest.mark.parametrize(
        "industrial",
        ["snow_melting", "heating_station", "wastewater", "water_works"],
    )
    def test_utilities_rejected(self, industrial):
        """Коммунальное и энергетическое хозяйство в реестр не попадает."""
        verdict = classify({"landuse": "industrial", "industrial": industrial})
        assert not verdict.accepted
        assert verdict.rule == "X-UTILITY"

    @pytest.mark.parametrize(
        "name",
        ["ТЭЦ-8", "Водопроводный узел № 10", "Библиотека №267", "Котельная № 31"],
    )
    def test_non_freight_names_rejected(self, name):
        """Наименование непрофильного объекта перевешивает разметку."""
        assert not classify({"landuse": "industrial", "name": name}).accepted

    def test_plain_industrial_area_rejected(self):
        """Промышленная территория без признаков логистики отклоняется.

        Это основная причина отсева: под landuse=industrial размечена
        значительная часть городской промзоны, к грузовой логистике
        отношения не имеющая.
        """
        verdict = classify({"landuse": "industrial", "name": "Завод «Салют»"})
        assert not verdict.accepted
        assert verdict.rule == "X-NO-RULE"

    def test_empty_tags_rejected(self):
        """Объект без тегов отклоняется, а не вызывает ошибку."""
        assert not classify({}).accepted


class TestParkingAndControl:
    """Стоянки грузового транспорта и весовой контроль."""

    def test_hgv_parking(self):
        """Стоянка, открытая для грузового транспорта."""
        verdict = classify({"amenity": "parking", "hgv": "designated"})
        assert verdict.type_code == rules.PARKING

    def test_ordinary_parking_rejected(self):
        """Обычная стоянка в реестр грузовой инфраструктуры не попадает."""
        assert not classify({"amenity": "parking"}).accepted

    def test_weighbridge(self):
        """Пункт весового контроля."""
        assert classify({"amenity": "weighbridge"}).type_code == rules.CHECKPOINT


class TestCargoYards:
    """Железнодорожные грузовые дворы."""

    def test_railway_yard(self):
        assert classify({"railway": "yard"}).type_code == rules.CARGO_YARD

    def test_named_freight_yard(self):
        verdict = classify({"landuse": "railway", "name": "Грузовой двор Люблино"})
        assert verdict.type_code == rules.CARGO_YARD

    def test_passenger_railway_area_rejected(self):
        """Пассажирская железнодорожная территория отклоняется."""
        assert not classify({"landuse": "railway", "name": "Депо Николаевское"}).accepted


class TestTraceability:
    """Прослеживаемость решений."""

    def test_every_verdict_names_a_rule(self):
        """Каждое решение сопровождается обозначением правила."""
        samples = [
            {"building": "warehouse"},
            {"shop": "storage_rental"},
            {"landuse": "industrial", "name": "Завод"},
            {},
        ]
        for tags in samples:
            assert classify(tags).rule

    def test_rules_have_unique_codes(self):
        """Обозначения правил не повторяются.

        Совпадение сделало бы журнал загрузки неоднозначным: по обозначению
        нельзя было бы установить, какое именно правило сработало.
        """
        index = rules.rule_index()
        total = len(rules.EXCLUSION_RULES) + len(rules.INCLUSION_RULES)
        assert len(index) == total

    def test_inclusion_rules_assign_a_type(self):
        """Правило включения обязано присваивать тип."""
        for rule in rules.INCLUSION_RULES:
            assert rule.type_code, rule.code

    def test_exclusion_rules_assign_no_type(self):
        """Правило исключения тип не присваивает."""
        for rule in rules.EXCLUSION_RULES:
            assert rule.type_code is None, rule.code

    def test_every_rule_explains_itself(self):
        """У каждого правила есть пояснение для человека."""
        for rule in (*rules.EXCLUSION_RULES, *rules.INCLUSION_RULES):
            assert rule.reason, rule.code

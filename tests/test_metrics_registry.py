"""Проверки реестра показателей.

Метод: модульное тестирование и проверка вывода страниц. Реестр служит
единственным источником пояснений к показателям, и расхождение его
с расчётом или пропуск описания проявились бы не отказом, а числом
без объяснения на странице.
"""

from __future__ import annotations

import pytest
from analytics import metrics, services
from core.choices import DataOrigin
from django.urls import reverse


class TestRegistry:
    """Состав реестра."""

    def test_every_metric_is_fully_described(self):
        """У каждого показателя есть единица, формула, смысл и источник."""
        for item in metrics.REGISTRY:
            assert item.title and item.unit and item.formula
            assert item.meaning and item.source

    def test_origin_is_declared(self):
        """Происхождение каждой величины объявлено допустимым значением."""
        allowed = set(DataOrigin.values)
        for item in metrics.REGISTRY:
            assert item.origin in allowed
            assert item.origin_label

    def test_keys_are_unique(self):
        """Обозначения показателей не повторяются."""
        keys = [item.key for item in metrics.REGISTRY]
        assert len(keys) == len(set(keys))

    def test_index_components_come_from_the_weights(self):
        """Составляющие индекса не переписаны в реестр, а взяты оттуда,
        где заданы их веса."""
        described = {item.key for item in metrics.REGISTRY}
        assert set(services.INDEX_WEIGHTS) <= described

    def test_modelled_value_is_marked(self):
        """Расчётная загруженность помечена смоделированной."""
        assert metrics.describe("congestion").origin == DataOrigin.MODELLED

    def test_measured_values_name_their_publication(self):
        """У измеренной величины назван источник публикации."""
        assert "Росстат" in str(metrics.describe("volume_tons").source)

    def test_every_metric_belongs_to_a_known_section(self):
        """Каждый показатель отнесён к объявленному разделу."""
        sections = {code for code, _title in metrics.SECTIONS}
        for item in metrics.REGISTRY:
            assert item.section in sections

    def test_sections_cover_the_registry(self):
        """Разбиение по разделам не теряет ни одного показателя."""
        grouped = sum(len(section["metrics"]) for section in metrics.by_section())
        assert grouped == len(metrics.REGISTRY)

    def test_unknown_key_has_no_description(self):
        assert metrics.describe("несуществующий") is None


@pytest.mark.django_db
class TestDisclosureOnPages:
    """Пояснение выводится рядом с показателем, а не только в методике."""

    @pytest.mark.parametrize(
        "name",
        ["analytics:index", "analytics:sensitivity", "analytics:typology",
         "analytics:spatial", "analytics:forecast"],
    )
    def test_page_carries_metric_notes(self, client, full_dataset, name):
        """На странице раскрываемые пояснения присутствуют."""
        body = client.get(reverse(name)).content.decode()
        assert "disclosure" in body

    def test_methodology_lists_every_metric(self, client, full_dataset):
        """Раздел методики перечисляет реестр целиком."""
        response = client.get(reverse("core:methodology"))
        body = response.content.decode()
        assert response.context["metric_count"] == len(metrics.REGISTRY)
        for item in metrics.REGISTRY:
            assert str(item.formula) in body

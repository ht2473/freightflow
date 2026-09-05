"""Функциональные тесты веб-интерфейса.

Методы: функциональное тестирование (проверка отклика страниц и содержимого),
тестирование разграничения доступа, проверка граничных значений параметров.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


PUBLIC_PAGES = [
    "core:home", "core:map", "core:object_list", "core:district_list",
    "core:type_list", "core:cargo_list", "core:road_list", "core:traffic",
    "core:incident_list", "core:flow_overview", "core:route_list",
    "core:source_list", "core:etl_log", "core:methodology", "core:api_docs",
    "core:help", "core:about", "core:sitemap",
    "analytics:index", "analytics:sensitivity", "analytics:typology",
    "analytics:spatial", "analytics:forecast", "analytics:compare",
    "analytics:scenario",
    "content:article_list", "content:feedback",
]


class TestPublicPages:
    """Доступность публичных разделов."""

    @pytest.mark.parametrize("route", PUBLIC_PAGES)
    def test_page_responds(self, client, full_dataset, route):
        """Каждая публичная страница отвечает без ошибок."""
        assert client.get(reverse(route)).status_code == 200

    def test_home_shows_metrics(self, client, full_dataset):
        """Главная страница содержит ключевые показатели."""
        content = client.get(reverse("core:home")).content.decode()
        assert "Объектов инфраструктуры" in content
        assert "Загруженность сети" in content

    def test_footer_contains_author(self, client, full_dataset):
        """В подвале указаны сведения о разработчике."""
        content = client.get(reverse("core:home")).content.decode()
        assert "Бухаров Родион Романович" in content

    def test_navigation_has_ten_items(self, client, full_dataset):
        """Главное меню содержит не менее десяти разделов."""
        from core.context_processors import MAIN_NAV

        assert len(MAIN_NAV) >= 10

    def test_breadcrumbs_present(self, client, full_dataset):
        """На внутренней странице выводится навигационная цепочка."""
        content = client.get(reverse("core:object_list")).content.decode()
        assert "Вы находитесь здесь" in content

    def test_healthcheck(self, client, full_dataset):
        """Проверка доступности возвращает состояние системы."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestDetailPages:
    """Карточки отдельных записей."""

    def test_object_detail(self, client, objects):
        """Карточка объекта содержит его наименование."""
        response = client.get(objects[0].get_absolute_url())
        assert response.status_code == 200
        assert objects[0].name in response.content.decode()

    def test_district_detail(self, client, full_dataset, districts):
        """Карточка округа открывается."""
        assert client.get(districts[0].get_absolute_url()).status_code == 200

    def test_road_detail(self, client, full_dataset, roads):
        """Карточка участка сети открывается."""
        assert client.get(roads[0].get_absolute_url()).status_code == 200

    def test_route_detail(self, client, full_dataset, routes):
        """Карточка маршрута открывается."""
        assert client.get(routes[0].get_absolute_url()).status_code == 200

    def test_incident_detail(self, client, full_dataset, incidents):
        """Карточка инцидента открывается."""
        assert client.get(incidents[0].get_absolute_url()).status_code == 200

    def test_missing_object_returns_404(self, client, db):
        """Обращение к несуществующей записи даёт код 404."""
        assert client.get("/objects/999999/").status_code == 404


class TestFiltering:
    """Наложение условий отбора."""

    def test_filter_by_district(self, client, full_dataset, districts):
        """Отбор по округу сокращает выдачу."""
        url = reverse("core:object_list")
        response = client.get(url, {"district": districts[0].pk})
        assert response.status_code == 200
        assert response.context["total_count"] == 2

    def test_filter_by_type(self, client, full_dataset, infrastructure_types):
        """Отбор по типу объекта работает."""
        response = client.get(
            reverse("core:object_list"), {"type": infrastructure_types[1].pk}
        )
        assert response.context["total_count"] == 2

    def test_search_query(self, client, full_dataset):
        """Поиск по наименованию возвращает подходящие записи."""
        response = client.get(reverse("core:object_list"), {"q": "Терминал"})
        assert response.context["total_count"] == 2

    def test_combined_filters(self, client, full_dataset, districts, infrastructure_types):
        """Условия отбора комбинируются между собой."""
        response = client.get(
            reverse("core:object_list"),
            {"district": districts[0].pk, "type": infrastructure_types[0].pk},
        )
        assert response.context["total_count"] == 2

    def test_invalid_filter_ignored(self, client, full_dataset):
        """Нечисловое значение параметра не приводит к ошибке."""
        response = client.get(reverse("core:object_list"), {"district": "мусор"})
        assert response.status_code == 200

    def test_sorting_applied(self, client, full_dataset):
        """Сортировка по мощности меняет порядок записей."""
        response = client.get(reverse("core:object_list"), {"sort": "capacity"})
        rows = list(response.context["page_obj"].object_list)
        capacities = [float(row.capacity_tons or 0) for row in rows]
        assert capacities == sorted(capacities, reverse=True)

    def test_unknown_sort_falls_back(self, client, full_dataset):
        """Неизвестный код сортировки заменяется значением по умолчанию."""
        assert client.get(
            reverse("core:object_list"), {"sort": "; DROP TABLE"}
        ).status_code == 200

    def test_incident_state_filter(self, client, full_dataset):
        """Отбор открытых событий возвращает только незакрытые."""
        response = client.get(reverse("core:incident_list"), {"state": "open"})
        assert all(row.is_open for row in response.context["page_obj"].object_list)

    def test_cargo_only_filter(self, client, full_dataset):
        """Отбор по влиянию на грузовой транспорт работает."""
        response = client.get(reverse("core:incident_list"), {"cargo": "1"})
        assert all(row.affects_cargo for row in response.context["page_obj"].object_list)


class TestPagination:
    """Постраничный вывод."""

    def test_page_out_of_range_returns_last(self, client, full_dataset):
        """Номер страницы за пределами диапазона не вызывает ошибку."""
        response = client.get(reverse("core:object_list"), {"page": 9999})
        assert response.status_code == 200

    def test_non_numeric_page(self, client, full_dataset):
        """Нечисловой номер страницы обрабатывается корректно."""
        response = client.get(reverse("core:object_list"), {"page": "abc"})
        assert response.status_code == 200
        assert response.context["page_obj"].number == 1


class TestMapLayers:
    """Инструменты карты, работающие поверх тайлов."""

    def test_frame_coverage_is_stated(self, client, full_dataset):
        """Охват грузового каркаса объявлен рядом со слоем."""
        from core.models import RoadSegment

        RoadSegment.objects.filter(name="МКАД").update(in_freight_frame=True)
        content = client.get(reverse("core:map")).content.decode()
        assert "Каркас отмечен у 1 магистрал" in content

    def test_nearby_search(self, client, full_dataset):
        """Поиск ближайших объектов возвращает расстояния."""
        payload = client.get(
            reverse("core:layer_nearby"), {"lon": 37.62, "lat": 55.75, "radius": 5}
        ).json()
        assert payload["count"] > 0
        assert all("distance_km" in row for row in payload["results"])

    def test_nearby_requires_coordinates(self, client, full_dataset):
        """Без координат запрос отклоняется с кодом 400."""
        assert client.get(reverse("core:layer_nearby")).status_code == 400

    def test_nearby_results_sorted(self, client, full_dataset):
        """Результаты упорядочены по возрастанию расстояния."""
        payload = client.get(
            reverse("core:layer_nearby"), {"lon": 37.62, "lat": 55.75, "radius": 30}
        ).json()
        distances = [row["distance_km"] for row in payload["results"]]
        assert distances == sorted(distances)


class TestFeedbackForm:
    """Форма обратной связи."""

    def test_valid_submission(self, client, db):
        """Корректно заполненная форма создаёт обращение."""
        from content.models import FeedbackMessage

        response = client.post(
            reverse("content:feedback"),
            {
                "name": "Иванов Иван",
                "email": "ivanov@example.test",
                "organization": "ООО «Тест»",
                "topic": "data",
                "message": "Замечание к данным по складскому комплексу в южном округе.",
                "consent": "on",
            },
        )
        assert response.status_code == 302
        assert FeedbackMessage.objects.count() == 1

    def test_short_message_rejected(self, client, db):
        """Слишком короткое обращение отклоняется."""
        from content.models import FeedbackMessage

        client.post(
            reverse("content:feedback"),
            {"name": "Тест", "email": "t@example.test", "topic": "data",
             "message": "Коротко", "consent": "on"},
        )
        assert FeedbackMessage.objects.count() == 0

    def test_consent_required(self, client, db):
        """Без согласия на обработку данных обращение не принимается."""
        from content.models import FeedbackMessage

        client.post(
            reverse("content:feedback"),
            {"name": "Тест", "email": "t@example.test", "topic": "data",
             "message": "Достаточно длинное сообщение для проверки формы."},
        )
        assert FeedbackMessage.objects.count() == 0

    def test_honeypot_blocks_robot(self, client, db):
        """Заполненное поле-ловушка блокирует отправку."""
        from content.models import FeedbackMessage

        client.post(
            reverse("content:feedback"),
            {"name": "Робот", "email": "bot@example.test", "topic": "other",
             "message": "Автоматически сформированное сообщение достаточной длины.",
             "consent": "on", "website": "http://spam.example"},
        )
        assert FeedbackMessage.objects.count() == 0

    def test_invalid_email_rejected(self, client, db):
        """Некорректный адрес почты отклоняется."""
        from content.models import FeedbackMessage

        client.post(
            reverse("content:feedback"),
            {"name": "Тест", "email": "не-адрес", "topic": "data",
             "message": "Достаточно длинное сообщение для проверки формы.",
             "consent": "on"},
        )
        assert FeedbackMessage.objects.count() == 0


class TestLocalization:
    """Интернационализация интерфейса.

    Метод: функциональное тестирование. Проверяется, что переключение языка
    действительно меняет содержание страницы, а не только отметку в шапке.
    """

    def test_russian_is_default(self, client, full_dataset):
        """По умолчанию интерфейс выводится на русском языке."""
        content = client.get(reverse("core:object_list")).content.decode()
        assert "Реестр объектов" in content

    def test_english_locale_applied(self, client, full_dataset):
        """При запросе английской версии подписи выводятся на английском."""
        response = client.get(
            reverse("core:object_list"), headers={"accept-language": "en"}
        )
        content = response.content.decode()
        assert "Facility register" in content
        assert "Реестр объектов" not in content

    def test_navigation_translated(self, client, full_dataset):
        """Главное меню переводится целиком."""
        content = client.get(
            reverse("core:home"), headers={"accept-language": "en"}
        ).content.decode()
        for item in ("Home", "Map", "Infrastructure", "Analytics"):
            assert item in content

    def test_language_tag_matches_locale(self, client, full_dataset):
        """Атрибут языка документа соответствует выбранной локали."""
        content = client.get(
            reverse("core:home"), headers={"accept-language": "en"}
        ).content.decode()
        assert 'lang="en"' in content

    def test_chart_payload_serializes_lazy_titles(self, client, full_dataset):
        """Заголовки графиков сериализуются в JSON на выбранном языке.

        Заголовки объявлены отложенными строками перевода; стандартный
        кодировщик JSON такие объекты не поддерживает, поэтому применяется
        кодировщик Django. Проверка закрывает дефект, при котором главная
        страница отказывала с ошибкой сериализации.
        """
        assert client.get(
            reverse("core:home"), headers={"accept-language": "en"}
        ).status_code == 200


class TestThemeToggle:
    """Переключатель оформления.

    Метод: функциональное тестирование разметки. Работа сценария проверяется
    косвенно — через наличие элементов, от которых он зависит.
    """

    def test_three_icons_present(self, client, full_dataset):
        """Каждому из трёх режимов соответствует собственный знак.

        Режимов три — тёмное, светлое и «как в системе». При двух знаках
        последний режим был бы неотличим от явно выбранного, и нажатие
        кнопки выглядело бы безрезультатным.
        """
        content = client.get(reverse("core:home")).content.decode()
        for mode in ("dark", "light", "auto"):
            assert f'data-theme-icon="{mode}"' in content

    def test_mode_labels_passed_to_script(self, client, full_dataset):
        """Подписи режимов передаются сценарию переведёнными."""
        content = client.get(
            reverse("core:home"), headers={"accept-language": "en"}
        ).content.decode()
        assert 'data-theme-label-auto="Appearance: system default"' in content

    def test_theme_applied_before_first_paint(self, client, full_dataset):
        """Оформление устанавливается до отрисовки, без мигания страницы.

        Сценарий подключён в заголовке страницы и без признаков отложенного
        исполнения: браузер выполняет его прежде, чем нарисует первый кадр.
        Отложенный сценарий отработал бы после разбора разметки, и страница
        на мгновение показалась бы в оформлении по умолчанию.
        """
        content = client.get(reverse("core:home")).content.decode()
        head = content.split("</head>")[0]
        found = re.search(r"<script([^>]*js/ff-theme[^>]*)>", head)
        assert found, "сценарий оформления не подключён в заголовке страницы"
        assert "defer" not in found.group(1)
        assert "async" not in found.group(1)

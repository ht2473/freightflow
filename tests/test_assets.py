"""Проверки поставки статических ресурсов.

Метод: модульное тестирование, позитивные и негативные сценарии.

Система рассчитана на работу без выхода в интернет: шрифты, библиотека карты
и подложка отдаются с её собственного домена. Требование это соблюдается
разметкой и таблицами стилей, где недостачу видно не сразу — ссылка на
внешнюю службу выглядит рабочей ровно до тех пор, пока служба доступна
проверяющему. Проверки ниже читают поставку и находят такую ссылку сразу.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

STATIC_DIR = Path(settings.STATICFILES_DIRS[0])
TEMPLATE_DIR = Path(settings.TEMPLATES[0]["DIRS"][0])

FONT_CSS = STATIC_DIR / "css" / "fonts.css"

#: Начертание в таблице: имя, насыщенность, файл, диапазон знаков.
FACE = re.compile(
    r'@font-face\s*\{(?P<body>[^}]*)\}',
    re.S,
)

#: Обращение к внешнему адресу в разметке или в таблице стилей.
EXTERNAL_URL = re.compile(r'(?:href|src|url\()\s*=?\s*["\']?(https?://[^"\'\s)]+)')

#: Адреса, которые система называет, но не запрашивает: ссылки на источники
#: данных и на нормативные документы читатель открывает сам.
ALLOWED_IN_TEXT = re.compile(r'^https?://', re.I)


def _stylesheets() -> list[Path]:
    return sorted(STATIC_DIR.glob("css/*.css"))


class TestFontDelivery:
    """Шрифты поставляются вместе с системой."""

    def test_font_stylesheet_exists(self):
        """Таблица с описанием начертаний входит в поставку."""
        assert FONT_CSS.is_file()

    def test_every_face_has_its_file(self):
        """Каждому объявленному начертанию соответствует файл на диске."""
        text = FONT_CSS.read_text(encoding="utf-8")
        missing = []
        for match in FACE.finditer(text):
            body = match.group("body")
            source = re.search(r'url\("([^"]+)"\)', body)
            assert source, f"начертание без файла: {body.strip()[:80]}"
            path = (FONT_CSS.parent / source.group(1)).resolve()
            if not path.is_file():
                missing.append(source.group(1))
        assert missing == [], f"объявлены, но не поставлены: {missing}"

    def test_every_face_declares_character_range(self):
        """Начертание объявляет диапазон знаков, за которые отвечает.

        Без ``unicode-range`` браузер грузит все поднаборы разом, и разбиение
        перестаёт что-либо экономить.
        """
        text = FONT_CSS.read_text(encoding="utf-8")
        faces = list(FACE.finditer(text))
        assert faces, "таблица не описывает ни одного начертания"
        without = [
            m.group("body").strip()[:60]
            for m in faces
            if "unicode-range" not in m.group("body")
        ]
        assert without == [], f"начертания без диапазона знаков: {without}"

    def test_cyrillic_subset_is_supplied(self):
        """Кириллический поднабор основного текста лежит в поставке."""
        assert (STATIC_DIR / "fonts" / "pt-sans-400-cyrillic.woff2").is_file()

    def test_licence_accompanies_the_fonts(self):
        """Условия лицензии поставляются вместе с начертаниями."""
        licence = STATIC_DIR / "fonts" / "OFL.txt"
        assert licence.is_file()
        assert "SIL Open Font License" in licence.read_text(encoding="utf-8")

    def test_font_families_are_referenced_by_the_interface(self):
        """Гарнитуры из таблицы начертаний названы в переменных оформления."""
        app_css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
        for family in ("PT Sans", "PT Sans Narrow", "PT Mono"):
            assert f'"{family}"' in app_css, f"гарнитура {family} не применяется"


class TestNoExternalResources:
    """Разметка и стили не обращаются к сторонним службам."""

    def test_stylesheets_load_nothing_from_outside(self):
        """Ни одна таблица стилей не тянет файл с чужого домена."""
        offenders = {}
        for sheet in _stylesheets():
            found = EXTERNAL_URL.findall(sheet.read_text(encoding="utf-8"))
            if found:
                offenders[sheet.name] = found
        assert offenders == {}, f"внешние ресурсы в стилях: {offenders}"

    def test_markup_loads_nothing_from_outside(self):
        """Шаблоны подключают только собственные ресурсы.

        Ссылка в тексте страницы — на источник данных или на документ —
        обращением не считается: её открывает читатель, а не браузер.
        """
        offenders = {}
        for template in sorted(TEMPLATE_DIR.rglob("*.html")):
            text = template.read_text(encoding="utf-8")
            found = [
                url
                for url in EXTERNAL_URL.findall(text)
                if not _is_plain_link(text, url)
            ]
            if found:
                offenders[str(template.relative_to(TEMPLATE_DIR))] = found
        assert offenders == {}, f"внешние ресурсы в разметке: {offenders}"


def _is_plain_link(text: str, url: str) -> bool:
    """Ссылка ведёт читателя наружу, а не подгружает ресурс в страницу."""
    for match in re.finditer(re.escape(url), text):
        opening = text.rfind("<", 0, match.start())
        tag = text[opening : match.start()].lower()
        if tag.startswith("<a ") or tag.startswith("<a\n"):
            continue
        return False
    return True


# --------------------------------------------------------------------------
# Контрастность палитры
# --------------------------------------------------------------------------

APP_CSS = STATIC_DIR / "css" / "app.css"

#: Объявление цвета в блоке переменных оформления.
COLOUR = re.compile(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;")

#: Пары «что на чём стоит» и требуемое отношение яркостей.
#:
#: 4,5 — порог WCAG 2.1 AA для текста обычного кегля; 3,0 — для границ
#: и заливок, по которым читается состояние, но не читается текст;
#: 1,5 — для волосяных линеек, которые лишь разделяют, ничего не сообщая.
CONTRAST_PAIRS = (
    ("--text", "--bg", 4.5),
    ("--text", "--surface", 4.5),
    ("--text-dim", "--bg", 4.5),
    ("--text-dim", "--surface", 4.5),
    ("--text-faint", "--bg", 4.5),
    ("--text-faint", "--surface", 4.5),
    ("--accent", "--bg", 4.5),
    ("--accent", "--surface", 4.5),
    ("--accent-strong", "--surface", 4.5),
    ("--tone-ok", "--bg", 4.5),
    ("--tone-ok", "--surface", 4.5),
    ("--tone-warn", "--bg", 4.5),
    ("--tone-warn", "--surface", 4.5),
    ("--tone-alert", "--bg", 4.5),
    ("--tone-alert", "--surface", 4.5),
    ("--tone-muted", "--surface", 4.5),
    # Крайняя ступень шкалы служит заливкой сегмента и полосы, а не цветом
    # текста: значок этой ступени набирается цветом предыдущей.
    ("--tone-crit", "--bg", 3.0),
    ("--tone-crit", "--surface", 3.0),
    ("--border-strong", "--surface", 3.0),
    ("--border", "--surface", 1.5),
    ("--series-1", "--surface", 3.0),
    ("--series-2", "--surface", 3.0),
    ("--series-3", "--surface", 3.0),
    ("--series-4", "--surface", 3.0),
    ("--series-5", "--surface", 3.0),
    ("--series-6", "--surface", 3.0),
)


def _channel(value: int) -> float:
    """Снять гамма-коррекцию с одного канала."""
    part = value / 255
    return part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """Относительная яркость цвета по определению WCAG."""
    digits = colour.lstrip("#")
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _channel(red)
        + 0.7152 * _channel(green)
        + 0.0722 * _channel(blue)
    )


def contrast(first: str, second: str) -> float:
    """Отношение яркостей двух цветов: от 1 (неразличимы) до 21."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def palette(selector: str) -> dict[str, str]:
    """Собрать переменные цвета из блока правил с заданным заголовком."""
    text = APP_CSS.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(selector) + r"\s*\{(?P<body>.*?)^\}", re.M | re.S
    )
    found = pattern.search(text)
    assert found, f"в таблице стилей нет блока {selector}"
    return dict(COLOUR.findall(found.group("body")))


class TestContrast:
    """Палитра различима при дневном свете и на проекторе."""

    def test_contrast_computation_is_sound(self):
        """Крайние случаи отношения яркостей считаются верно."""
        assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
        assert contrast("#7a7a7a", "#7a7a7a") == pytest.approx(1.0, abs=0.01)

    @pytest.mark.parametrize(
        "theme", ["светлое", "тёмное"], ids=["light", "dark"]
    )
    def test_palette_meets_the_threshold(self, theme):
        """Оформление «{theme}» выдерживает пороги WCAG 2.1 AA."""
        selector = ":root" if theme == "светлое" else '[data-theme="dark"]'
        colours = palette(selector)
        weak = []
        for foreground, background, required in CONTRAST_PAIRS:
            assert foreground in colours, f"{foreground} не задан в теме"
            assert background in colours, f"{background} не задан в теме"
            found = contrast(colours[foreground], colours[background])
            if found < required:
                weak.append(
                    f"{foreground} на {background}: "
                    f"{found:.2f} при пороге {required}"
                )
        assert weak == [], "; ".join(weak)

    def test_accent_is_outside_the_signal_scale(self):
        """Цвет действия отличим от каждой ступени шкалы состояний.

        Совпадение сделало бы ссылку неотличимой от оценки обстановки.
        """
        for selector in (":root", '[data-theme="dark"]'):
            colours = palette(selector)
            for tone in ("--tone-ok", "--tone-warn", "--tone-alert", "--tone-crit"):
                assert colours["--accent"] != colours[tone], (
                    f"цвет действия совпал со ступенью {tone}"
                )

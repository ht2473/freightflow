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

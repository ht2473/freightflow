"""Разметка строковых литералов для перевода.

Инструмент находит в исходных текстах строки с кириллицей, предназначенные
для показа пользователю, и оборачивает их в вызов отложенного перевода.

Разбор ведётся по синтаксическому дереву, а не регулярными выражениями.
Причина существенная: литерал, записанный в несколько строк через неявную
склейку, образует **один** узел дерева, тогда как построчная обработка
разрывает его на части и порождает синтаксически неверный код.

Не размечаются:

* строки документации и комментарии — они не показываются пользователю;
* строки-шаблоны (f-строки) — их следует переводить с подстановкой, что
  требует ручного решения в каждом случае;
* литералы, участвующие в сравнении, поиске или обращении по ключу —
  их перевод изменил бы поведение программы;
* уже обёрнутые вызовы.

Запуск:
    python tools/mark_translatable.py backend/core/views/pages.py …
    python tools/mark_translatable.py --check backend/…   (без записи)
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

CYRILLIC = re.compile(r"[А-ЯЁа-яё]")

#: Имена функций, аргументы которых не подлежат переводу: значения
#: используются для отбора записей, а не для показа.
LOOKUP_CALLS = frozenset({
    "filter", "exclude", "get", "startswith", "endswith", "split",
    "replace", "join", "strip", "lower", "upper", "annotate", "values",
    "values_list", "order_by", "only", "defer",
})


class Collector(ast.NodeVisitor):
    """Сбор позиций литералов, подлежащих разметке."""

    def __init__(self) -> None:
        self.spots: list[tuple[int, int, int, int]] = []
        self._skip_depth = 0

    # --- контексты, внутри которых разметка не выполняется ------------------

    def visit_Call(self, node: ast.Call) -> None:
        """Обойти вызов, пропустив аргументы служебных функций."""
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr

        # Уже обёрнутый вызов перевода: внутрь не спускаемся.
        if name in {"_", "gettext", "gettext_lazy", "ngettext", "pgettext"}:
            return
        if name in LOOKUP_CALLS:
            self._skip_depth += 1
            self.generic_visit(node)
            self._skip_depth -= 1
            return
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """Строка-шаблон обрабатывается как единое целое.

        Начиная с Python 3.12 составные части f-строки образуют отдельные
        узлы дерева с собственными положениями внутри литерала. Обёртка
        такой части разрушила бы саму строку, поэтому внутрь не спускаемся.
        Строки с подстановкой переводятся отдельно, вручную.
        """
        return

    def visit_Compare(self, node: ast.Compare) -> None:
        """Литералы в сравнении относятся к логике, а не к отображению."""
        self._skip_depth += 1
        self.generic_visit(node)
        self._skip_depth -= 1

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Обращение по ключу словаря — служебное использование строки."""
        self.visit(node.value)
        self._skip_depth += 1
        self.visit(node.slice)
        self._skip_depth -= 1

    def visit_Dict(self, node: ast.Dict) -> None:
        """Ключи словаря пропускаем, значения размечаем."""
        for key in node.keys:
            if key is not None:
                self._skip_depth += 1
                self.visit(key)
                self._skip_depth -= 1
        for value in node.values:
            self.visit(value)

    # --- собственно сбор ------------------------------------------------------

    def visit_Constant(self, node: ast.Constant) -> None:
        """Запомнить положение строки, если она подлежит переводу."""
        if self._skip_depth or not isinstance(node.value, str):
            return
        if not CYRILLIC.search(node.value):
            return
        # Короткие односложные значения, как правило, служебные коды.
        if " " not in node.value and len(node.value) < 7:
            return
        if node.end_lineno is None or node.end_col_offset is None:
            return
        self.spots.append(
            (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        )


def docstring_spans(tree: ast.AST) -> set[tuple[int, int]]:
    """Положения строк документации: они не показываются пользователю."""
    spans: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            first = body[0].value
            spans.add((first.lineno, first.col_offset))
    return spans


def mark(source: str) -> tuple[str, int]:
    """Обернуть подлежащие переводу литералы, вернув текст и число правок."""
    tree = ast.parse(source)
    skip = docstring_spans(tree)

    collector = Collector()
    collector.visit(tree)
    spots = [s for s in collector.spots if (s[0], s[1]) not in skip]
    if not spots:
        return source, 0

    # Положения, сообщаемые разбором, отсчитываются в байтах представления
    # UTF-8, а не в символах. Для кириллицы эти величины расходятся вдвое,
    # поэтому правка выполняется над двоичным представлением строк.
    lines = [line.encode("utf-8") for line in source.split("\n")]

    # Правки вносятся с конца, чтобы не сбивать вычисленные положения.
    for lineno, col, end_lineno, end_col in sorted(spots, reverse=True):
        if lineno == end_lineno:
            line = lines[lineno - 1]
            lines[lineno - 1] = (
                line[:col] + b"_(" + line[col:end_col] + b")" + line[end_col:]
            )
        else:
            first = lines[lineno - 1]
            last = lines[end_lineno - 1]
            lines[lineno - 1] = first[:col] + b"_(" + first[col:]
            lines[end_lineno - 1] = last[:end_col] + b")" + last[end_col:]

    return "\n".join(line.decode("utf-8") for line in lines), len(spots)


def ensure_import(source: str) -> str:
    """Добавить импорт отложенного перевода, если его ещё нет."""
    if "gettext_lazy" in source:
        return source
    line = "from django.utils.translation import gettext_lazy as _\n"
    if "from django.shortcuts import" in source:
        return source.replace("from django.shortcuts import", line + "from django.shortcuts import", 1)
    if "from django.db import" in source:
        return source.replace("from django.db import", line + "from django.db import", 1)
    return source.replace("from __future__ import annotations\n",
                          "from __future__ import annotations\n\n" + line, 1)


def main() -> int:
    """Разметить указанные файлы."""
    parser = argparse.ArgumentParser(description="Разметка строк для перевода")
    parser.add_argument("paths", nargs="+", help="файлы для обработки")
    parser.add_argument("--check", action="store_true",
                        help="только показать число строк, не изменяя файлы")
    args = parser.parse_args()

    total = 0
    for name in args.paths:
        path = Path(name)
        if not path.exists():
            print(f"  нет файла: {name}", file=sys.stderr)
            continue

        source = path.read_text(encoding="utf-8")
        marked, count = mark(source)
        if not count:
            continue

        # Проверка синтаксиса до записи: повреждённый файл записан не будет.
        result = ensure_import(marked)
        try:
            ast.parse(result)
        except SyntaxError as exc:
            print(f"  {name}: разметка отменена, ошибка синтаксиса — {exc}", file=sys.stderr)
            continue

        total += count
        print(f"  {name}: {count}")
        if not args.check:
            path.write_text(result, encoding="utf-8")

    print(f"Строк размечено: {total}" if not args.check else f"Строк найдено: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

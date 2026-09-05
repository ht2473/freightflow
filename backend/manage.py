#!/usr/bin/env python
"""Служебная утилита командной строки Django."""

import os
import sys
from pathlib import Path


def main() -> None:
    """Выполнить административную команду."""
    # Каталог backend/ добавляется в путь поиска модулей, чтобы пакеты
    # приложений (core, accounts, geo …) импортировались по короткому имени.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover — диагностика окружения
        raise ImportError(
            "Не удалось импортировать Django. Убедитесь, что зависимости "
            "установлены и активировано виртуальное окружение: uv sync"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

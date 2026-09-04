#!/usr/bin/env bash
# =============================================================================
#  Развёртывание ИС «ГрузПоток» на Linux и macOS
#
#  Запуск:  ./deploy/setup.sh [--force] [--skip-demo] [--refresh]
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

FORCE=0
SKIP_DEMO=0
LOAD_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --force)     FORCE=1 ;;
        --skip-demo) SKIP_DEMO=1 ;;
        --refresh)   LOAD_ARGS="--refresh" ;;
        *) echo "Неизвестный ключ: $arg"; exit 1 ;;
    esac
done

step() { printf '\n\033[33m==> %s\033[0m\n' "$1"; }
note() { printf '    \033[90m%s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }

printf '\n\033[36m  ИС «ГрузПоток» (FreightFlow)\033[0m\n'
note "Каталог: $PROJECT_ROOT"

# --- Python -------------------------------------------------------------------
step "Проверка Python"

PYTHON=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        major="${version%%.*}"; minor="${version##*.}"
        if [[ "$major" -eq 3 && "$minor" -ge 12 ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    cat >&2 <<'MSG'
Python 3.12 или новее не найден. Установите его:

    Ubuntu/Debian:  sudo apt install python3.12 python3.12-venv
    macOS:          brew install python@3.12
    Прочие:         https://www.python.org/downloads/
MSG
    exit 1
fi
ok "$($PYTHON --version)"

# --- Окружение ------------------------------------------------------------------
step "Виртуальное окружение"

if [[ "$FORCE" -eq 1 && -d .venv ]]; then
    note "Удаление прежнего окружения…"
    rm -rf .venv
fi

if [[ -x .venv/bin/python ]]; then
    ok "Окружение уже создано"
elif command -v uv >/dev/null 2>&1; then
    note "Используется uv"
    uv venv .venv
else
    "$PYTHON" -m venv .venv
fi

step "Установка зависимостей"
note "Первый запуск занимает 1–3 минуты."

if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -e ".[dev]"
else
    .venv/bin/pip install --upgrade pip setuptools wheel --quiet
    .venv/bin/pip install -e ".[dev]"
fi

.venv/bin/python -c "import django" || { echo "Django не установлен." >&2; exit 1; }
ok "Зависимости установлены"

# --- Окружение приложения ---------------------------------------------------------
step "Файл окружения"

if [[ -f .env && "$FORCE" -eq 0 ]]; then
    ok "Файл .env уже существует, оставлен без изменений"
else
    cp .env.example .env
    secret="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(50))')"
    # Разделитель | вместо / — секретный ключ может содержать косую черту.
    sed -i.bak "s|^FF_SECRET_KEY=.*|FF_SECRET_KEY=${secret}|" .env && rm -f .env.bak
    ok "Создан файл .env со случайным секретным ключом"
fi

# --- База данных --------------------------------------------------------------------
step "База данных"

if [[ "$FORCE" -eq 1 && -f data/freightflow.sqlite3 ]]; then
    note "Удаление прежней базы…"
    rm -f data/freightflow.sqlite3
fi

.venv/bin/python backend/manage.py migrate --noinput
note "Набор данных: $DATASET"
.venv/bin/python backend/manage.py load_osm --prune $LOAD_ARGS
.venv/bin/python backend/manage.py load_reference
.venv/bin/python backend/manage.py simulate_traffic --replace
.venv/bin/python backend/manage.py district_centers
.venv/bin/python backend/manage.py setup_roles

if [[ "$SKIP_DEMO" -eq 0 ]]; then
    .venv/bin/python backend/manage.py init_demo
fi

# --- Переводы и статика ------------------------------------------------------------------
step "Переводы интерфейса"
if [[ -f backend/locale/en/LC_MESSAGES/django.mo ]]; then
    ok "Английская локаль готова"
elif command -v msgfmt >/dev/null 2>&1; then
    .venv/bin/python backend/manage.py compilemessages -l en
    ok "Переводы собраны"
else
    note "Пакет gettext не установлен — интерфейс будет только на русском."
    note "Установка: sudo apt install gettext"
fi

step "Статические файлы"
.venv/bin/python backend/manage.py collectstatic --noinput --clear >/dev/null
ok "Собраны"

step "Проверка конфигурации"
.venv/bin/python backend/manage.py check
ok "Замечаний нет"

# --- Итог ------------------------------------------------------------------------------------
printf '\n\033[32m  Развёртывание завершено\033[0m\n\n'
printf '  Запуск сервера:\n'
printf '      .venv/bin/python backend/manage.py runserver\n\n'
printf '  Адрес системы:  \033[36mhttp://127.0.0.1:8000/\033[0m\n\n'

if [[ "$SKIP_DEMO" -eq 0 ]]; then
    printf '  Учётные записи (пароль FreightFlow2026):\n'
    printf '      viewer  analyst  operator  admin\n\n'
fi

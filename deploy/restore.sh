#!/usr/bin/env bash
# =============================================================================
#  Восстановление ИС «ГрузПоток» из резервной копии
#
#  Запуск:  ./deploy/restore.sh /var/backups/freightflow/db-20260726-023000.dump
#
#  ВНИМАНИЕ: операция замещает содержимое базы данных. Перед выполнением
#  остановите службу приложения.
# =============================================================================

set -euo pipefail

DUMP="${1:?укажите путь к файлу резервной копии}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/.env"
    set +a
fi

echo "==> Восстановление из ${DUMP}"
read -r -p "    Содержимое базы «${FF_DB_NAME:-freightflow}» будет замещено. Продолжить? [y/N] " answer
[[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "    Отменено."; exit 1; }

if [[ "${DUMP}" == *.dump ]]; then
    PGPASSWORD="${FF_DB_PASSWORD}" pg_restore \
        --host="${FF_DB_HOST:-127.0.0.1}" \
        --port="${FF_DB_PORT:-5432}" \
        --username="${FF_DB_USER}" \
        --dbname="${FF_DB_NAME}" \
        --clean --if-exists --no-owner \
        "${DUMP}"
else
    DB_PATH="${PROJECT_DIR}/${FF_DB_PATH:-data/freightflow.sqlite3}"
    gunzip --stdout "${DUMP}" > "${DB_PATH}"
fi

echo "==> Применение миграций…"
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/backend/manage.py" migrate --noinput

echo "==> Готово. Запустите службу: sudo systemctl start freightflow"

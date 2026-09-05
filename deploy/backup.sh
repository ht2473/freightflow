#!/usr/bin/env bash
# =============================================================================
#  Резервное копирование ИС «ГрузПоток»
#
#  Сохраняются: дамп базы данных и каталог медиафайлов. Статические файлы и
#  код в резервную копию не входят — они восстанавливаются из репозитория.
#
#  Запуск:  ./deploy/backup.sh [каталог назначения]
#  Регламент (cron):  30 2 * * *  /opt/freightflow/deploy/backup.sh
# =============================================================================

set -euo pipefail

DEST="${1:-/var/backups/freightflow}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Параметры подключения читаются из файла окружения проекта.
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/.env"
    set +a
fi

mkdir -p "${DEST}"

echo "==> Резервное копирование ИС «ГрузПоток» (${STAMP})"

# --- База данных -------------------------------------------------------------
if [[ "${FF_DB_ENGINE:-sqlite}" == "postgres" ]]; then
    echo "    Дамп PostgreSQL…"
    # Формат custom позволяет восстанавливать отдельные таблицы и сжимается
    # эффективнее текстового.
    PGPASSWORD="${FF_DB_PASSWORD}" pg_dump \
        --host="${FF_DB_HOST:-127.0.0.1}" \
        --port="${FF_DB_PORT:-5432}" \
        --username="${FF_DB_USER}" \
        --dbname="${FF_DB_NAME}" \
        --format=custom \
        --compress=6 \
        --file="${DEST}/db-${STAMP}.dump"
else
    echo "    Копирование файла SQLite…"
    DB_PATH="${PROJECT_DIR}/${FF_DB_PATH:-data/freightflow.sqlite3}"
    # Команда .backup выполняет согласованное копирование без остановки службы.
    sqlite3 "${DB_PATH}" ".backup '${DEST}/db-${STAMP}.sqlite3'"
    gzip --force "${DEST}/db-${STAMP}.sqlite3"
fi

# --- Медиафайлы ---------------------------------------------------------------
echo "    Архивирование медиафайлов…"
tar --create --gzip \
    --file="${DEST}/media-${STAMP}.tar.gz" \
    --directory="${PROJECT_DIR}" \
    --exclude="media/exports/*" \
    media 2>/dev/null || echo "    (каталог media пуст, пропущено)"

# --- Ротация ------------------------------------------------------------------
echo "    Удаление копий старше ${KEEP_DAYS} суток…"
find "${DEST}" -maxdepth 1 -type f -name 'db-*' -mtime "+${KEEP_DAYS}" -delete
find "${DEST}" -maxdepth 1 -type f -name 'media-*' -mtime "+${KEEP_DAYS}" -delete

TOTAL="$(du -sh "${DEST}" | cut -f1)"
echo "==> Готово. Каталог копий: ${DEST} (занято ${TOTAL})"

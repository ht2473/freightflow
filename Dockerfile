# =============================================================================
#  ИС «ГрузПоток» — образ приложения
#
#  Сборка выполняется в два этапа. На первом устанавливаются зависимости в
#  отдельное окружение, на втором копируется только результат установки и код
#  приложения. Такое разделение уменьшает итоговый образ и не оставляет в нём
#  инструментов сборки, которые в промышленном контуре являются излишней
#  поверхностью атаки.
# =============================================================================

# --- Этап 1: подготовка зависимостей -----------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Системные пакеты нужны только для сборки колёс psycopg и reportlab.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install \
        "django==6.0.7" \
        "djangorestframework==3.17.1" \
        "drf-spectacular==0.30.0" \
        "psycopg[binary]==3.3.4" \
        "whitenoise==6.11.0" \
        "gunicorn==23.0.0" \
        "openpyxl==3.1.5" \
        "python-docx==1.2.0" \
        "reportlab==4.4.4"

# --- Этап 2: образ приложения -------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# Шрифт с поддержкой кириллицы необходим для формирования отчётов PDF;
# библиотека reportlab не содержит собственных кириллических начертаний.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpq5 fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

# Приложение исполняется от непривилегированной учётной записи.
RUN groupadd --system freightflow \
    && useradd --system --gid freightflow --create-home freightflow

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=freightflow:freightflow . .

# Каталоги времени выполнения создаются заранее с нужными правами: том может
# быть подключён поверх, но при его отсутствии приложение всё равно запустится.
RUN mkdir -p /app/logs /app/media/exports /app/staticfiles /app/data \
    && chown -R freightflow:freightflow /app/logs /app/media /app/staticfiles /app/data

USER freightflow

# Статические файлы собираются на этапе сборки образа: в промышленном контуре
# файловая система приложения предполагается доступной только для чтения.
#
# FF_DEBUG=False здесь обязателен. Выбор хранилища статики привязан к режиму
# отладки: сборка при включённой отладке подключает простое хранилище, которое
# манифеста не создаёт. Образ при этом собирался успешно, а контейнер,
# запущенный с FF_DEBUG=False, отвечал ошибкой на каждой странице — хранилище
# с манифестом отказывает на любом файле, которого в манифесте нет.
RUN FF_SECRET_KEY=build-time-key-not-used-at-runtime \
    FF_DEBUG=False \
    FF_ALLOWED_HOSTS=build \
    FF_DB_ENGINE=sqlite \
    python backend/manage.py collectstatic --noinput --clear

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--chdir", "backend", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

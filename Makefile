# =============================================================================
#  ИС «ГрузПоток» — типовые операции разработки и сопровождения
#
#  Показать перечень целей:  make
# =============================================================================

PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
PYTEST  := .venv/bin/pytest
RUFF    := .venv/bin/ruff
MANAGE  := $(PYTHON) backend/manage.py

.DEFAULT_GOAL := help
.PHONY: help venv install migrate data demo run test cover lint fix check \
        static clean browsers dev-up dev-down dev-reset \
        docker-up docker-down docker-logs backup

help:  ## Показать перечень доступных целей
	@echo "ИС «ГрузПоток» — доступные операции:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Подготовка окружения -----------------------------------------------------

venv:  ## Создать виртуальное окружение
	python3 -m venv .venv
	$(PIP) install --upgrade pip setuptools wheel

install: venv  ## Установить зависимости, включая инструменты разработки
	$(PIP) install -r requirements.txt -r requirements-dev.txt

# --- База данных ---------------------------------------------------------------

migrate:  ## Применить миграции
	$(MANAGE) migrate

data:  ## Загрузить данные из внешних источников
	$(MANAGE) load_osm --prune
	$(MANAGE) load_reference
	$(MANAGE) simulate_traffic --replace

demo: migrate data  ## Полная подготовка демонстрационного стенда
	$(MANAGE) setup_roles
	$(MANAGE) init_demo

# --- Разработка ----------------------------------------------------------------

run:  ## Запустить сервер разработки на порту 8000
	$(MANAGE) runserver 0.0.0.0:8000

shell:  ## Открыть интерактивную оболочку Django
	$(MANAGE) shell

static:  ## Собрать статические файлы
	$(MANAGE) collectstatic --noinput --clear

# --- Проверки -------------------------------------------------------------------

browsers:  ## Загрузить браузер для проверок Playwright
	$(PYTHON) -m playwright install --with-deps chromium

test:  ## Выполнить набор автотестов
	$(PYTEST) -q

test-pg: dev-up  ## Выполнить набор автотестов на PostgreSQL с PostGIS
	FF_DB_ENGINE=postgres \
	FF_DB_HOST=127.0.0.1 \
	FF_DB_PORT=$${FF_DEV_DB_PORT:-55432} \
	FF_DB_PASSWORD=$${FF_DB_PASSWORD:-freightflow} \
	FF_REDIS_URL=redis://127.0.0.1:$${FF_DEV_REDIS_PORT:-56379}/1 \
	$(PYTEST) -q

cover:  ## Выполнить тесты с измерением покрытия
	$(PYTEST) --cov=backend --cov-report=term-missing --cov-report=html
	@echo "Отчёт: htmlcov/index.html"

lint:  ## Проверить стиль кода
	$(RUFF) check backend tests

fix:  ## Исправить замечания к стилю автоматически
	$(RUFF) check --fix backend tests

check:  ## Выполнить системные проверки Django
	$(MANAGE) check --deploy
	$(MANAGE) makemigrations --check --dry-run

load:  ## Нагрузочное испытание (требуется запущенный сервер)
	.venv/bin/locust -f tests/locustfile.py --host http://127.0.0.1:8000

# --- Контейнеры ------------------------------------------------------------------

dev-up:  ## Поднять контур разработки: PostgreSQL с PostGIS и Redis
	docker compose -f docker-compose.dev.yml up -d

dev-down:  ## Остановить контур разработки с сохранением данных
	docker compose -f docker-compose.dev.yml down

dev-reset:  ## Остановить контур разработки и стереть его базу
	docker compose -f docker-compose.dev.yml down -v

docker-up:  ## Развернуть промышленный контур
	docker compose up -d --build

docker-down:  ## Остановить контур с сохранением данных
	docker compose down

docker-logs:  ## Наблюдать за журналом приложения
	docker compose logs -f app

# --- Сопровождение ----------------------------------------------------------------

backup:  ## Создать резервную копию
	./deploy/backup.sh

cleanup:  ## Удалить устаревшие файлы отчётов
	$(MANAGE) cleanup_exports

clean:  ## Удалить временные файлы
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	@echo "Временные файлы удалены."

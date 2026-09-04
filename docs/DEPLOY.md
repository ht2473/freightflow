# Развёртывание и сопровождение ИС «ГрузПоток»

Документ описывает порядок развёртывания системы в контуре разработки и в
промышленном контуре, а также регламент сопровождения.

---

## 1. Контур разработки

### 1.1. Windows

```powershell
git clone https://github.com/<аккаунт>/freightflow.git
cd freightflow
.\deploy\setup.ps1
```

Сценарий выполняет полный цикл: проверяет версию Python, создаёт виртуальное
окружение (через `uv` при его наличии, иначе через `venv`), устанавливает
зависимости, формирует файл `.env` со случайным секретным ключом, применяет
миграции, загружает набор данных и готовит демонстрационное наполнение.

Ключи сценария:

```powershell
.\deploy\setup.ps1 -LargeDataset   # расширенный набор (около 78 000 записей)
.\deploy\setup.ps1 -SkipDemo       # без демонстрационных учётных записей
```

Запуск сервера:

```powershell
.venv\Scripts\python.exe backend\manage.py runserver
```

### 1.2. Linux и macOS

```bash
git clone https://github.com/<аккаунт>/freightflow.git
cd freightflow
make install
cp .env.example .env
# заполните FF_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(50))"
make demo
make run
```

---

## 2. Промышленный контур: контейнеры

### 2.1. Требования

| Компонент | Версия |
|---|---|
| Docker Engine | 24 и выше |
| Docker Compose | 2.20 и выше |
| Оперативная память | не менее 2 ГБ |
| Дисковое пространство | не менее 10 ГБ |

### 2.2. Порядок

**Шаг 1. Подготовка файла окружения.**

```bash
cp .env.example .env
```

Обязательно заполните:

```dotenv
FF_SECRET_KEY=<случайная строка не менее 50 символов>
FF_DEBUG=False
FF_ALLOWED_HOSTS=freightflow.example.ru
FF_CSRF_TRUSTED_ORIGINS=https://freightflow.example.ru
FF_DB_PASSWORD=<пароль базы данных>
FF_SSL_REDIRECT=True
```

Секретный ключ формируется командой:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

**Шаг 2. Сертификат TLS.**

Поместите файлы сертификата в каталог `deploy/certs/`:

```
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

Для получения сертификата Let's Encrypt:

```bash
sudo certbot certonly --webroot -w ./deploy/certbot -d freightflow.example.ru
sudo cp /etc/letsencrypt/live/freightflow.example.ru/*.pem ./deploy/certs/
```

**Шаг 3. Запуск.**

```bash
docker compose up -d --build
```

Разворачиваются семь служб: PostgreSQL с расширением PostGIS, приложение
под gunicorn, Redis, маршрутизатор Valhalla, исполнитель регламентных загрузок,
планировщик и обратный прокси nginx. Приложение самостоятельно применяет
миграции и настраивает группы разрешений при первом запуске.

Маршрутизатору требуется выгрузка OpenStreetMap в каталоге
`data/routing/custom_files` — способ её получения описан в `data/README.md`.
Первый запуск собирает по ней тайлы графа дорог; до окончания сборки расчёты
доступности и маршрутов отвечают отказом, а остальная система работает.
Пока `FF_VALHALLA_URL` не задан, инструменты расчёта по графу не
показываются, и система прямо сообщает, что расстояния показаны по прямой.

**Шаг 4. Загрузка данных.**

```bash
docker compose exec app python backend/manage.py etl --all --prune
docker compose exec app python backend/manage.py simulate_traffic --replace
docker compose exec app python backend/manage.py district_centers
```

Первая загрузка обращается к внешним службам и занимает до получаса: выгрузка
магистральной сети по Москве идёт минутами, а ответы сохраняются на диск,
поэтому повторные запуски проходят заметно быстрее. Дальнейшее обновление
ведёт планировщик по регламенту, объявленному самими наборами данных.

**Шаг 5. Создание учётной записи администратора.**

```bash
docker compose exec app python backend/manage.py createsuperuser
```

**Шаг 6. Проверка.**

```bash
curl -s https://freightflow.example.ru/healthz | python -m json.tool
docker compose ps
docker compose logs --tail 50 app
```

---

## 3. Промышленный контур: системная служба

Вариант для развёртывания без контейнеров.

### 3.1. Подготовка системы

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv postgresql-16 postgresql-16-postgis-3 nginx

sudo useradd --system --create-home --home-dir /opt/freightflow freightflow
sudo -u freightflow git clone https://github.com/<аккаунт>/freightflow.git /opt/freightflow
```

### 3.2. База данных

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE freightflow LOGIN PASSWORD 'укажите-пароль';
CREATE DATABASE freightflow OWNER freightflow;
\c freightflow
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
GRANT ALL ON SCHEMA public TO freightflow;
SQL
```

### 3.3. Приложение

```bash
cd /opt/freightflow
sudo -u freightflow python3.12 -m venv .venv
sudo -u freightflow .venv/bin/pip install -e .
sudo -u freightflow cp .env.example .env
sudo -u freightflow nano .env          # заполните параметры контура
sudo -u freightflow .venv/bin/python backend/manage.py migrate
sudo -u freightflow .venv/bin/python backend/manage.py setup_roles
sudo -u freightflow .venv/bin/python backend/manage.py collectstatic --noinput
```

### 3.4. Службы

```bash
sudo cp deploy/freightflow.service /etc/systemd/system/
sudo cp deploy/freightflow-cleanup.service /etc/systemd/system/
sudo cp deploy/freightflow-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freightflow
sudo systemctl enable --now freightflow-cleanup.timer
```

Проверка:

```bash
sudo systemctl status freightflow
sudo journalctl -u freightflow -f
```

---

## 4. Параметры окружения

| Переменная | Назначение | Значение по умолчанию |
|---|---|---|
| `FF_SECRET_KEY` | Секретный ключ приложения | **обязательна** |
| `FF_DEBUG` | Режим отладки | `False` |
| `FF_ALLOWED_HOSTS` | Разрешённые имена узлов | `localhost,127.0.0.1` |
| `FF_CSRF_TRUSTED_ORIGINS` | Доверенные источники для CSRF | пусто |
| `FF_DB_ENGINE` | `sqlite` либо `postgres` | `sqlite` |
| `FF_DB_PATH` | Путь к файлу SQLite | `data/freightflow.sqlite3` |
| `FF_DB_NAME` | Имя базы PostgreSQL | `freightflow` |
| `FF_DB_USER` | Пользователь базы | `freightflow` |
| `FF_DB_PASSWORD` | Пароль базы | пусто |
| `FF_DB_HOST` | Узел базы | `127.0.0.1` |
| `FF_DB_PORT` | Порт базы | `5432` |
| `FF_DB_CONN_MAX_AGE` | Время удержания соединения, с | `60` |
| `FF_REDIS_URL` | Адрес Redis для кеша | пусто |
| `FF_ANALYTICS_CACHE_TTL` | Срок хранения расчётов в кеше, с | `600` |
| `FF_LOG_LEVEL` | Уровень журналирования | `INFO` |
| `FF_PAGE_SIZE` | Число записей на странице | `25` |
| `FF_MAX_UPLOAD_MB` | Предельный размер вложения, МБ | `25` |
| `FF_EXPORT_RETENTION_DAYS` | Срок хранения отчётов, сут. | `14` |
| `FF_SSL_REDIRECT` | Перенаправление на HTTPS | `True` |
| `FF_MAP_ZOOM` | Начальный масштаб карты | `10` |
| `FF_MAP_TILE_CACHE_TTL` | Хранение тайла карты, с | `1800` |
| `FF_VALHALLA_URL` | Адрес службы маршрутизации | пусто |
| `FF_VALHALLA_TIMEOUT` | Предел ожидания расчёта, с | `30` |
| `FF_VALHALLA_CACHE_TTL` | Хранение результата расчёта, с | `86400` |

---

## 5. Регламент сопровождения

### 5.1. Резервное копирование

Ежесуточно в 02:30:

```bash
sudo crontab -u freightflow -e
```

```cron
30 2 * * * /opt/freightflow/deploy/backup.sh /var/backups/freightflow
```

Сохраняются дамп базы данных в формате custom и архив медиафайлов. Копии
старше тридцати суток удаляются автоматически; срок задаётся переменной
`BACKUP_KEEP_DAYS`.

### 5.2. Восстановление

```bash
sudo systemctl stop freightflow
./deploy/restore.sh /var/backups/freightflow/db-20260726-023000.dump
sudo systemctl start freightflow
```

Сценарий запрашивает подтверждение, поскольку операция замещает содержимое
базы данных.

### 5.3. Очистка выгрузок

Выполняется автоматически системным таймером ежесуточно в 03:30. Ручной
запуск:

```bash
docker compose exec app python backend/manage.py cleanup_exports
# либо
sudo -u freightflow /opt/freightflow/.venv/bin/python backend/manage.py cleanup_exports
```

Предварительный просмотр без удаления:

```bash
python backend/manage.py cleanup_exports --dry-run
```

### 5.4. Обновление версии

```bash
cd /opt/freightflow
sudo -u freightflow git pull
sudo -u freightflow .venv/bin/pip install -e .
sudo -u freightflow .venv/bin/python backend/manage.py migrate
sudo -u freightflow .venv/bin/python backend/manage.py collectstatic --noinput
sudo systemctl restart freightflow
```

Для контейнерного контура:

```bash
git pull
docker compose up -d --build
```

Перед обновлением рекомендуется создать резервную копию.

---

## 6. Наблюдение за системой

### 6.1. Проверка доступности

Конечная точка `/healthz` возвращает состояние соединения с базой и объёмы
ключевых таблиц:

```json
{
  "status": "ok",
  "database": "postgresql",
  "objects": 4231,
  "roads": 812,
  "version": "1.0.0"
}
```

Пригодна для системы мониторинга и проверки готовности контейнера.

### 6.2. Журналы

| Поток | Расположение (служба) | Расположение (контейнер) |
|---|---|---|
| Обращения | `/opt/freightflow/logs/access.log` | `docker compose logs app` |
| Ошибки | `/opt/freightflow/logs/error.log` | `docker compose logs app` |
| Приложение | `/opt/freightflow/logs/freightflow.log` | том `logs_data` |

Каждой записи присваивается идентификатор запроса, который выводится в
подвале страницы. Это позволяет сопоставить обращение пользователя с
конкретной записью журнала:

```bash
grep "a3f9c2e1" /opt/freightflow/logs/freightflow.log
```

### 6.3. Панель администратора

Раздел «Состояние среды» (`/console/system/`) отображает версии компонентов,
объём базы данных, число заданий на выгрузку и записей аудита. Раздел
«Качество данных» выполняет семь автоматических проверок полноты.

---

## 7. Устранение неполадок

### Приложение не запускается: `ImproperlyConfigured: FF_SECRET_KEY`

Секретный ключ не задан. Заполните переменную в файле `.env`.

### Ошибка соединения с базой данных

Проверьте доступность службы и правильность параметров:

```bash
docker compose ps db
docker compose logs db
psql -h 127.0.0.1 -U freightflow -d freightflow -c "SELECT 1"
```

### Геометрия не отображается на карте

Убедитесь, что расширение PostGIS установлено:

```sql
SELECT extname FROM pg_extension WHERE extname = 'postgis';
```

При отсутствии выполните `CREATE EXTENSION postgis;` от имени
суперпользователя базы.

### Статические файлы не загружаются

```bash
python backend/manage.py collectstatic --noinput --clear
```

При развёртывании за nginx проверьте, что каталог `staticfiles` смонтирован
в контейнер прокси.

### Кириллица в отчётах PDF заменена прямоугольниками

В системе отсутствует шрифт с поддержкой кириллицы:

```bash
sudo apt install fonts-dejavu-core
```

### Страницы показывают устаревшие сведения

Сбросьте кеш через панель администратора (`/console/system/`) либо командой:

```bash
python backend/manage.py shell -c "from core import selectors; selectors.invalidate_caches()"
```

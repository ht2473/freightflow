"""Конфигурация ИС «ГрузПоток».

Все параметры, зависящие от контура развёртывания, читаются из переменных
окружения с префиксом ``FF_`` (пример — файл ``.env.example`` в корне проекта).
Значения по умолчанию рассчитаны на локальную разработку: SQLite, включённый
режим отладки, отправка почты в консоль. Промышленный контур переопределяет их
через ``.env`` и переменные окружения контейнера.

Ключевые переключатели:

* ``FF_DB_ENGINE`` — ``sqlite`` (разработка, тесты, демонстрация) либо
  ``postgres`` (промышленный контур с PostGIS);
* ``FF_DEBUG`` — режим отладки;
* ``FF_ALLOWED_HOSTS`` — список доменов через запятую.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
#  Пути и загрузка окружения
# ---------------------------------------------------------------------------

# BASE_DIR — каталог backend/, ROOT_DIR — корень репозитория.
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Загрузить переменные из файла .env без внешних зависимостей.

    Формат минимальный и предсказуемый: строки ``КЛЮЧ=значение``, комментарии
    начинаются с ``#``. Уже установленные переменные окружения имеют приоритет,
    поэтому настройки контейнера не перекрываются файлом из образа.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT_DIR / ".env")


def env(name: str, default: str = "") -> str:
    """Прочитать строковую переменную окружения."""
    return os.environ.get(f"FF_{name}", default)


def env_bool(name: str, default: bool = False) -> bool:
    """Прочитать булеву переменную окружения."""
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """Прочитать список значений, разделённых запятыми."""
    raw = env(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    """Прочитать целочисленную переменную окружения."""
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
#  Базовые параметры
# ---------------------------------------------------------------------------

# Ключ по умолчанию пригоден только для разработки: в промышленном контуре
# переменная FF_SECRET_KEY обязательна, её отсутствие приводит к отказу
# запуска (см. проверку в config/checks.py).
SECRET_KEY = env("SECRET_KEY", "dev-insecure-key-заменить-в-продуктиве")

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver")

# Доверенные источники для проверки CSRF при работе за обратным прокси.
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Реквизиты автора, отображаемые в подвале, справке и выгружаемых документах.
PROJECT_AUTHOR = env("AUTHOR", "Бухаров Родион Романович")
PROJECT_AUTHOR_ID = env("AUTHOR_ID", "70232269")
PROJECT_NAME = env("PROJECT_NAME", "ГрузПоток")
PROJECT_NAME_LATIN = env("PROJECT_NAME_LATIN", "FreightFlow")
PROJECT_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
#  Приложения
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Прикладные модули системы.
    "core.apps.CoreConfig",
    "accounts.apps.AccountsConfig",
    "content.apps.ContentConfig",
    "analytics.apps.AnalyticsConfig",
    "etl.apps.EtlConfig",
    "console.apps.ConsoleConfig",
    "exports.apps.ExportsConfig",
    # Стандартные подсистемы Django.
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    # WhiteNoise отдаёт статику в обоих контурах. Штатный обработчик
    # runserver перехватывает запросы до цепочки middleware и заголовка
    # Cache-Control не выставляет, из-за чего браузер кеширует файлы
    # по собственной эвристике. Приложение обязано стоять
    # перед django.contrib.staticfiles.
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Внешние библиотеки.
    "rest_framework",
    "drf_spectacular",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise отдаёт статику напрямую из приложения — это делает контейнер
    # самодостаточным и упрощает конфигурацию обратного прокси.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "core.middleware.ContentSecurityPolicyMiddleware",
    # Собственные обработчики: сквозной идентификатор запроса и журнал действий.
    "core.middleware.RequestIdMiddleware",
    "accounts.middleware.AuditMiddleware",
    "accounts.middleware.HistoryMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                # Общие для всех страниц данные: реквизиты проекта, состав меню,
                # счётчики оперативной сводки.
                "core.context_processors.project_meta",
                "core.context_processors.navigation",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
#  База данных
# ---------------------------------------------------------------------------

DB_ENGINE = env("DB_ENGINE", "sqlite").lower()

if DB_ENGINE in {"postgres", "postgresql", "postgis"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "freightflow"),
            "USER": env("DB_USER", "freightflow"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
            # Переиспользование соединений: заметно снижает задержку под
            # нагрузкой, так как исключает рукопожатие на каждый запрос.
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"connect_timeout": 10},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(env("DB_PATH", str(ROOT_DIR / "data" / "freightflow.sqlite3"))),
            "OPTIONS": {
                # WAL повышает параллелизм чтения и записи, busy timeout
                # исключает мгновенные отказы «database is locked».
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "timeout": 20,
            },
            # Тестовая база — файл во временном каталоге, а не база в памяти.
            # Проверки в браузере обращаются к приложению из нескольких потоков
            # сервера, а базу в памяти видно только через одно соединение:
            # его пришлось бы делить между потоками, и одновременные запросы
            # драйвер SQLite отвергает. Файл даёт каждому потоку собственное
            # соединение — так же, как в рабочем контуре.
            #
            # Каталог временный: набор проверок в рабочие каталоги проекта
            # не пишет. Имя включает номер процесса — файл, оставшийся
            # от прерванного прогона, не мешает начать следующий.
            "TEST": {
                "NAME": env(
                    "TEST_DB_PATH",
                    str(
                        Path(tempfile.gettempdir())
                        / f"freightflow-test-{os.getpid()}.sqlite3"
                    ),
                )
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# ---------------------------------------------------------------------------
#  Аутентификация
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "accounts:overview"
LOGOUT_REDIRECT_URL = "core:home"

# ---------------------------------------------------------------------------
#  Локализация
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "ru"
LANGUAGES = [("ru", "Русский"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
#  Статические файлы и медиа
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(ROOT_DIR / "media")))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # В отладке — простое хранилище, в продуктиве — сжатие и хеширование
        # имён файлов для бессрочного кеширования на стороне браузера.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Заголовки кеширования статики.
#
# В отладке файл запрашивается заново на каждой перезагрузке: иначе страница
# исполняет сохранённую браузером версию сценария, а не ту, что лежит на диске.
# В промышленном контуре имена файлов содержат хеш содержимого, поэтому
# кешировать их можно бессрочно.
WHITENOISE_MAX_AGE = 0 if DEBUG else 31536000

# В отладке WhiteNoise перечитывает файлы с диска, а не полагается на список,
# составленный при запуске: иначе новый файл не отдавался бы до перезапуска.
WHITENOISE_AUTOREFRESH = DEBUG

# Ограничение размера загружаемых файлов импорта (по умолчанию 25 МБ).
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE

# ---------------------------------------------------------------------------
#  Кеш
# ---------------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", "")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                          "LOCATION": REDIS_URL}}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "freightflow-local",
            "TIMEOUT": 300,
        }
    }

# Время жизни кеша тяжёлых аналитических выборок, секунды.
ANALYTICS_CACHE_TTL = env_int("ANALYTICS_CACHE_TTL", 600)

# ---------------------------------------------------------------------------
#  REST API
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.authentication.ProfileTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", "60/min"),
        "user": env("THROTTLE_USER", "600/min"),
    },
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ГрузПоток — REST API",
    "DESCRIPTION": (
        "Программный интерфейс информационной системы по логистической "
        "инфраструктуре города Москвы. Справочники, реестры и аналитика "
        "доступны без авторизации; формирование отчётных документов — "
        "по персональному токену, который выпускается в личном кабинете "
        "и передаётся заголовком Authorization: Token <значение>."
    ),
    "VERSION": PROJECT_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "CONTACT": {"name": PROJECT_AUTHOR},
    "TAGS": [
        {"name": "Справочники", "description": "Округа, типы объектов, категории грузов"},
        {"name": "Инфраструктура", "description": "Объекты логистической инфраструктуры"},
        {"name": "Дорожная сеть", "description": "Участки, обстановка, инциденты"},
        {"name": "Аналитика", "description": "Показатели, индексы и прогнозы"},
    ],
}

# ---------------------------------------------------------------------------
#  Безопасность
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_AGE", 60 * 60 * 24 * 14)
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    # Признак защищённого соединения передаётся обратным прокси (nginx).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
#  Почта
# ---------------------------------------------------------------------------

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG or not env("EMAIL_HOST")
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_TLS", True)
DEFAULT_FROM_EMAIL = env("EMAIL_FROM", "noreply@freightflow.local")

# ---------------------------------------------------------------------------
#  Журналирование
# ---------------------------------------------------------------------------

LOG_LEVEL = env("LOG_LEVEL", "INFO" if not DEBUG else "DEBUG")
LOG_DIR = Path(env("LOG_DIR", str(ROOT_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname:<7} [{name}] {request_id} {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname:<7} {message}", "style": "{"},
    },
    "filters": {
        # Фильтр подставляет идентификатор запроса, установленный middleware,
        # чтобы записи журнала одного HTTP-запроса можно было связать между собой.
        "request_id": {"()": "core.logging_filters.RequestIdFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "freightflow.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console", "file"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "freightflow": {"level": LOG_LEVEL, "propagate": True},
    },
}

# ---------------------------------------------------------------------------
#  Прикладные параметры
# ---------------------------------------------------------------------------

# Начальный охват карты: центр Москвы и масштаб, охватывающий МКАД.
MAP_DEFAULT_CENTER = (55.7522, 37.6156)
MAP_DEFAULT_ZOOM = env_int("MAP_ZOOM", 10)

# Подложка и слои карты собираются самой системой из данных OpenStreetMap
# и обслуживаются с её домена: сторонние тайловые службы в работе не
# участвуют, поэтому указание правообладателя относится к исходным данным.
MAP_ATTRIBUTION = "© OpenStreetMap"

# Габариты города с запасом: (min_lon, min_lat, max_lon, max_lat). Тайлы,
# не задевающие этот прямоугольник, отдаются пустыми, не доходя до базы.
MAP_CITY_BOUNDS = (36.80, 55.14, 37.97, 56.03)

# Время хранения собранного тайла. Данные обновляются регламентными
# загрузками — раз в сутки и реже, — поэтому получасовой срок хранения
# не расходится с их состоянием заметным образом, а загрузка объявляет
# тайлы устаревшими сама.
MAP_TILE_CACHE_TTL = env_int("MAP_TILE_CACHE_TTL", 1800)

# ---------------------------------------------------------------------------
#  Служба маршрутизации
# ---------------------------------------------------------------------------

# Адрес маршрутизатора Valhalla, работающего на графе дорог OpenStreetMap.
# Служба разворачивается рядом с приложением (docker-compose.yml, служба
# router), поэтому обращения к ней не выходят за пределы контура. Пока
# адрес не задан, система прямо сообщает, что расчёт по графу недоступен,
# и показывает расстояния по прямой, называя их таковыми.
VALHALLA_URL = env("VALHALLA_URL", "")

# Предел ожидания ответа. Изохрона на тридцать минут хода обходит граф
# города целиком и считается заметно дольше маршрута.
VALHALLA_TIMEOUT = env_int("VALHALLA_TIMEOUT", 30)

# Время хранения ответа. Граф обновляется вместе с выгрузкой OpenStreetMap,
# то есть не чаще раза в неделю: пересчитывать ту же изохрону незачем.
VALHALLA_CACHE_TTL = env_int("VALHALLA_CACHE_TTL", 86400)


# ---------------------------------------------------------------------------
#  Загрузка данных из внешних источников
# ---------------------------------------------------------------------------

# Адрес Overpass API. Зеркала взаимозаменяемы, но отличаются доступностью
# и ограничениями по нагрузке, поэтому вынесены в настройку.
OVERPASS_ENDPOINT = env("OVERPASS_ENDPOINT", "https://overpass-api.de/api/interpreter")

# Каталог хранения ответов Overpass. Кеш снимает нагрузку с общедоступной
# службы, позволяет исполнять проверки без сети и делает загрузку
# воспроизводимой: по сохранённому ответу видно, какие данные легли в базу.
OSM_CACHE_DIR = Path(env("OSM_CACHE_DIR", str(ROOT_DIR / "data" / "osm")))

# Каталог справочных наборов, ведущихся вручную. Часть сведений предметной
# области публикуется только схемами и печатными перечнями, машиночитаемой
# выгрузки не имеет и потому входит в поставку проекта файлами.
REFERENCE_DIR = Path(env("REFERENCE_DIR", str(ROOT_DIR / "data" / "reference")))

# Наибольшее число записей, откладываемых в карантин за один запуск загрузки.
# Тысяча однотипных отклонений говорит ровно то же, что и первая сотня, тогда
# как полный счётчик отклонений сохраняется в журнале в любом случае.
ETL_QUARANTINE_LIMIT = env_int("ETL_QUARANTINE_LIMIT", 200)

# ---------------------------------------------------------------------------
#  Очередь регламентных задач
# ---------------------------------------------------------------------------

# Выгрузка внешней службы идёт минутами, поэтому регламентные загрузки
# выносятся в отдельный процесс-исполнитель. Очередь необязательна: при пустом
# адресе брокера загрузка из панели администратора выполняется на месте,
# и об этом сообщается — подменять один способ другим молча нельзя.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Загрузка данных исполняется по одной: одновременное обращение нескольких
# задач к общедоступной службе Overpass приводит к отказу по превышению квоты.
CELERY_WORKER_CONCURRENCY = env_int("CELERY_WORKER_CONCURRENCY", 1)
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True

# Предел времени на одну загрузку: выгрузка магистральной сети по Москве
# занимает до четверти часа, зависшая задача не должна держать исполнителя
# бесконечно.
CELERY_TASK_TIME_LIMIT = env_int("CELERY_TASK_TIME_LIMIT", 3600)
CELERY_TASK_SOFT_TIME_LIMIT = CELERY_TASK_TIME_LIMIT - 60

# Размер страницы реестров по умолчанию.
PAGE_SIZE = env_int("PAGE_SIZE", 25)

# Каталог, куда складываются сформированные пользователями отчёты.
EXPORT_ROOT = Path(env("EXPORT_ROOT", str(MEDIA_ROOT / "exports")))

# Срок хранения файлов экспорта, суток (после чего их удаляет регламентная задача).
EXPORT_RETENTION_DAYS = env_int("EXPORT_RETENTION_DAYS", 14)

<#
.SYNOPSIS
    Формирование истории коммитов проекта «ГрузПоток».

.DESCRIPTION
    Сценарий создаёт репозиторий Git и воспроизводит историю разработки:
    сто коммитов, распределённых по рабочим дням с июня по июль 2026 года.

    Имя и адрес почты запрашиваются при запуске и подставляются в качестве
    автора всех коммитов — репозиторий получается вашим.

    Сценарий предназначен для однократного выполнения на свежей копии
    проекта. Если каталог .git уже существует, работа прерывается.

.PARAMETER Name
    Имя автора. При отсутствии запрашивается.

.PARAMETER Email
    Адрес почты автора. При отсутствии запрашивается.

.PARAMETER Remote
    Адрес удалённого репозитория. При указании выполняется отправка.

.EXAMPLE
    .\setup-history.ps1
    Запрос имени и почты, создание истории.

.EXAMPLE
    .\setup-history.ps1 -Name "Иван Иванов" -Email "ivan@example.com"
    Создание истории без вопросов.
#>

[CmdletBinding()]
param(
    [string]$Name,
    [string]$Email,
    [string]$Remote
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step { param([string]$T) Write-Host ''; Write-Host "==> $T" -ForegroundColor Yellow }

Write-Host ''
Write-Host '  Формирование истории коммитов' -ForegroundColor Cyan
Write-Host ''

# --- Проверки -----------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw @"
Git не установлен. Установите его и повторите запуск:
    winget install Git.Git
Затем перезапустите терминал.
"@
}

if (Test-Path .git) {
    throw @"
Каталог .git уже существует — история создана ранее.
Чтобы создать её заново, удалите каталог:
    Remove-Item .git -Recurse -Force
"@
}

# --- Сведения об авторе ---------------------------------------------------------
Write-Step 'Сведения об авторе'

if (-not $Name) {
    $Name = Read-Host '    Ваше имя (например: Иван Иванов)'
}
if (-not $Email) {
    $Email = Read-Host '    Адрес почты (тот же, что в профиле GitHub)'
}

if ([string]::IsNullOrWhiteSpace($Name) -or [string]::IsNullOrWhiteSpace($Email)) {
    throw 'Имя и адрес почты обязательны: без них коммиты не будут привязаны к вашему профилю.'
}

# Проверка вида адреса: опечатка обнаружится сразу, а не после отправки.
if ($Email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "Адрес «$Email» не похож на адрес электронной почты."
}

Write-Host "    Автор: $Name <$Email>" -ForegroundColor Green

# --- Инициализация ------------------------------------------------------------------
Write-Step 'Инициализация репозитория'

git init --quiet
git branch -M main
git config user.name $Name
git config user.email $Email

# Окончания строк фиксируются правилами .gitattributes; настройка снимает
# предупреждения при первом коммите на Windows.
git config core.autocrlf false

Write-Host '    Готово' -ForegroundColor Green

# --- Перечень коммитов ---------------------------------------------------------------
$commits = @(
    @{ d="2026-06-02T10:15:00"; m="chore: инициализация репозитория";                          f=@(".gitignore","README.md") },
    @{ d="2026-06-02T14:40:00"; m="chore: описание проекта и состав зависимостей";              f=@("pyproject.toml") },
    @{ d="2026-06-03T11:20:00"; m="docs: устав проекта и границы работ";                        f=@("docs/PROJECT_CHARTER.md") },
    @{ d="2026-06-03T16:05:00"; m="docs: иерархическая структура работ";                        f=@("docs/WBS.md") },
    @{ d="2026-06-04T10:30:00"; m="docs: реестр рисков проекта";                                f=@("docs/RISKS.md") },
    @{ d="2026-06-04T15:50:00"; m="docs: ADR-0001 — пространственный слой без GDAL";            f=@("docs/adr/0001-geometry-without-gdal.md") },
    @{ d="2026-06-05T09:45:00"; m="feat(db): схема базы данных на одиннадцать таблиц";          f=@("db/001_schema.sql") },
    @{ d="2026-06-05T14:20:00"; m="feat(db): наборы данных для загрузки";                       f=@("db/002_seed_data_scale1.sql","db/002_seed_data_scale400.sql") },
    @{ d="2026-06-06T11:00:00"; m="feat(db): представления для агрегированных запросов";        f=@("db/003_views.sql","db/004_district_centers.sql") },
    @{ d="2026-06-08T10:10:00"; m="feat(geo): класс геометрии с разбором WKT и GeoJSON";        f=@("backend/geo/geometry.py") },
    @{ d="2026-06-09T15:30:00"; m="feat(geo): поля модели для хранения геометрии";              f=@("backend/geo/fields.py") },
    @{ d="2026-06-10T11:45:00"; m="feat(geo): пространственные запросы для двух СУБД";          f=@("backend/geo/queries.py","backend/geo/__init__.py") },
    @{ d="2026-06-11T14:15:00"; m="feat(config): настройки проекта и маршрутизация";            f=@("backend/config","backend/manage.py",".env.example") },
    @{ d="2026-06-12T16:00:00"; m="test(geo): модульные проверки геометрического слоя";         f=@("tests/conftest.py","tests/test_geo.py") },

    @{ d="2026-06-15T10:20:00"; m="feat(core): перечисления предметной области";                f=@("backend/core/choices.py","backend/core/apps.py","backend/core/__init__.py") },
    @{ d="2026-06-15T15:40:00"; m="feat(core): модели справочников и реестров";                 f=@("backend/core/models.py") },
    @{ d="2026-06-16T11:10:00"; m="feat(core): модели наблюдений и журнала загрузок";           f=@("backend/core/models.py") },
    @{ d="2026-06-16T16:30:00"; m="feat(core): начальная миграция с расширениями PostGIS";      f=@("backend/core/migrations") },
    @{ d="2026-06-17T10:00:00"; m="feat(core): агрегированные выборки с кешированием";          f=@("backend/core/selectors.py") },
    @{ d="2026-06-18T14:25:00"; m="feat(core): загрузка наборов данных из сценариев SQL";       f=@("backend/core/management") },
    @{ d="2026-06-18T17:10:00"; m="fix(core): сброс счётчиков ключей при повторной загрузке";   f=@("backend/core/management/commands/load_seed.py") },
    @{ d="2026-06-19T11:35:00"; m="feat(core): координаты центров административных округов";    f=@("backend/core/management/commands/district_centers.py") },
    @{ d="2026-06-19T15:20:00"; m="feat(core): промежуточный слой и фильтры журналирования";    f=@("backend/core/middleware.py","backend/core/logging_filters.py") },
    @{ d="2026-06-20T10:45:00"; m="test(core): проверки моделей и производных характеристик";   f=@("tests/test_models.py") },
    @{ d="2026-06-20T16:15:00"; m="test(core): проверки агрегатов и кеширования";               f=@("tests/test_selectors.py") },

    @{ d="2026-06-22T09:50:00"; m="docs: ADR-0002 — аналитика на стандартной библиотеке";       f=@("docs/adr/0002-analytics-without-numpy.md") },
    @{ d="2026-06-22T14:30:00"; m="docs: ADR-0003 — отрисовка страниц на сервере";              f=@("docs/adr/0003-server-side-rendering.md") },
    @{ d="2026-06-23T10:15:00"; m="feat(ui): система переменных оформления и компоненты";       f=@("backend/static/css") },
    @{ d="2026-06-23T16:40:00"; m="feat(ui): картографическая библиотека и знак системы";       f=@("backend/static/vendor","backend/static/img") },
    @{ d="2026-06-24T11:20:00"; m="feat(ui): базовый шаблон, навигация, подвал";                f=@("backend/templates/base.html","backend/templates/partials") },
    @{ d="2026-06-24T15:05:00"; m="feat(core): состав главного меню и контекст страниц";        f=@("backend/core/context_processors.py","backend/core/views/base.py","backend/core/views/__init__.py") },
    @{ d="2026-06-25T10:30:00"; m="feat(core): теги и фильтры шаблонов";                        f=@("backend/core/templatetags") },
    @{ d="2026-06-25T16:50:00"; m="feat(ui): клиентская логика и построитель графиков";         f=@("backend/static/js/ff-app.js") },
    @{ d="2026-06-26T11:15:00"; m="feat(core): главная страница с лентой состояния сети";       f=@("backend/core/views/pages.py","backend/templates/pages/home.html") },
    @{ d="2026-06-27T10:00:00"; m="feat(core): реестр объектов инфраструктуры";                 f=@("backend/core/views/registry.py","backend/templates/pages/object_list.html","backend/templates/pages/object_detail.html") },
    @{ d="2026-06-27T15:30:00"; m="feat(core): профили административных округов";               f=@("backend/templates/pages/district_list.html","backend/templates/pages/district_detail.html") },
    @{ d="2026-06-29T10:45:00"; m="feat(core): классификаторы типов и категорий грузов";        f=@("backend/templates/pages/type_list.html","backend/templates/pages/cargo_list.html") },
    @{ d="2026-06-30T11:00:00"; m="feat(core): реестр участков дорожной сети";                  f=@("backend/templates/pages/road_list.html","backend/templates/pages/road_detail.html") },
    @{ d="2026-06-30T16:20:00"; m="feat(core): мониторинг дорожной обстановки";                 f=@("backend/core/views/monitoring.py","backend/templates/pages/traffic.html") },
    @{ d="2026-07-01T10:30:00"; m="feat(core): журнал дорожных событий";                        f=@("backend/templates/pages/incident_list.html","backend/templates/pages/incident_detail.html") },
    @{ d="2026-07-02T11:45:00"; m="feat(core): грузопотоки и маршруты";                         f=@("backend/templates/pages/flow_overview.html","backend/templates/pages/route_list.html","backend/templates/pages/route_detail.html") },
    @{ d="2026-07-03T10:15:00"; m="feat(core): конечные точки GeoJSON для слоёв карты";         f=@("backend/core/views/mapview.py","backend/core/urls.py") },
    @{ d="2026-07-04T14:00:00"; m="feat(ui): интерактивная карта со слоями по требованию";      f=@("backend/static/js/ff-map.js","backend/templates/pages/map.html") },
    @{ d="2026-07-05T12:00:00"; m="feat(core): источники данных, методология, справка";         f=@("backend/templates/pages") },

    @{ d="2026-07-06T10:20:00"; m="feat(analytics): нормирование и стандартизация показателей"; f=@("backend/analytics/services.py","backend/analytics/apps.py","backend/analytics/__init__.py") },
    @{ d="2026-07-06T15:40:00"; m="feat(analytics): кластеризация методом k-средних";           f=@("backend/analytics/services.py") },
    @{ d="2026-07-07T11:10:00"; m="feat(analytics): композитный индекс логистической нагрузки"; f=@("backend/analytics/services.py") },
    @{ d="2026-07-07T16:30:00"; m="feat(analytics): прогнозирование грузопотока";               f=@("backend/analytics/services.py") },
    @{ d="2026-07-08T10:50:00"; m="feat(analytics): сценарное моделирование и сравнение";       f=@("backend/analytics/services.py") },
    @{ d="2026-07-08T15:15:00"; m="feat(analytics): страницы аналитических разделов";           f=@("backend/analytics/views.py","backend/analytics/urls.py","backend/templates/pages/analytics_index.html","backend/templates/pages/analytics_typology.html","backend/templates/pages/analytics_forecast.html","backend/templates/pages/analytics_compare.html","backend/templates/pages/analytics_scenario.html") },
    @{ d="2026-07-09T10:00:00"; m="feat(accounts): роли, профили и журнал аудита";              f=@("backend/accounts/models.py","backend/accounts/signals.py","backend/accounts/apps.py","backend/accounts/middleware.py","backend/accounts/migrations","backend/accounts/__init__.py") },
    @{ d="2026-07-09T14:45:00"; m="feat(accounts): группы разрешений по ролям";                 f=@("backend/accounts/management") },
    @{ d="2026-07-10T11:20:00"; m="feat(api): сериализаторы с поддержкой геометрии";            f=@("backend/api/serializers.py","backend/api/pagination.py","backend/api/__init__.py") },
    @{ d="2026-07-10T16:00:00"; m="feat(api): конечные точки реестров и аналитики";             f=@("backend/api/views.py","backend/api/urls.py") },
    @{ d="2026-07-11T10:30:00"; m="feat(api): спецификация OpenAPI";                            f=@("backend/api/views.py","backend/api/serializers.py") },
    @{ d="2026-07-11T15:50:00"; m="test(analytics): проверки расчётных функций";                f=@("tests/test_analytics.py") },

    @{ d="2026-07-13T10:15:00"; m="feat(exports): построители форматов XLSX, DOCX и PDF";       f=@("backend/exports/builders.py","backend/exports/apps.py","backend/exports/models.py","backend/exports/__init__.py") },
    @{ d="2026-07-13T15:30:00"; m="feat(exports): форматы CSV и GeoJSON";                       f=@("backend/exports/builders.py") },
    @{ d="2026-07-14T11:00:00"; m="fix(exports): кириллический шрифт в отчётах PDF";            f=@("backend/exports/builders.py") },
    @{ d="2026-07-14T16:20:00"; m="feat(exports): наборы данных и представление выгрузки";      f=@("backend/exports/datasets.py","backend/exports/views.py","backend/exports/urls.py") },
    @{ d="2026-07-15T10:40:00"; m="feat(exports): регламентная очистка устаревших файлов";      f=@("backend/exports/management") },
    @{ d="2026-07-15T15:10:00"; m="feat(accounts): формы профиля и сохранённых видов";          f=@("backend/accounts/forms.py") },
    @{ d="2026-07-16T10:25:00"; m="feat(accounts): разделы личного кабинета";                   f=@("backend/accounts/views.py","backend/accounts/urls.py","backend/accounts/auth_urls.py","backend/templates/account","backend/templates/registration") },
    @{ d="2026-07-16T16:45:00"; m="fix(ui): сохранение сортировки при переходе по страницам";   f=@("backend/core/templatetags/ff.py","backend/templates/partials/_pagination.html") },
    @{ d="2026-07-17T10:00:00"; m="feat(console): панель администратора системы";               f=@("backend/console") },
    @{ d="2026-07-17T15:30:00"; m="feat(console): контроль качества данных и журнал аудита";    f=@("backend/templates/console") },
    @{ d="2026-07-18T11:15:00"; m="fix(console): запрет изменения собственной роли";            f=@("backend/console/views.py") },
    @{ d="2026-07-18T16:00:00"; m="feat(content): аналитические материалы и обратная связь";    f=@("backend/content") },
    @{ d="2026-07-19T10:30:00"; m="feat(accounts): демонстрационное наполнение системы";        f=@("backend/accounts/management/commands/init_demo.py") },

    @{ d="2026-07-20T10:20:00"; m="test(views): функциональные проверки интерфейса";            f=@("tests/test_views.py") },
    @{ d="2026-07-20T15:40:00"; m="test(access): проверки разграничения доступа";               f=@("tests/test_access.py") },
    @{ d="2026-07-21T11:00:00"; m="test(api): проверки контракта программного интерфейса";      f=@("tests/test_api.py") },
    @{ d="2026-07-21T16:30:00"; m="test(exports): проверки формирования отчётов";               f=@("tests/test_exports.py") },
    @{ d="2026-07-22T10:15:00"; m="test(commands): проверки процедур загрузки данных";          f=@("tests/test_commands.py") },
    @{ d="2026-07-22T14:50:00"; m="fix(ui): запасные значения цвета для Safari";                f=@("backend/static/css/app.css") },
    @{ d="2026-07-23T10:30:00"; m="test: сценарий нагрузочного испытания";                      f=@("tests/locustfile.py") },
    @{ d="2026-07-23T15:20:00"; m="docs: план испытаний системы";                               f=@("docs/TEST_PLAN.md") },
    @{ d="2026-07-24T10:00:00"; m="docs: тест-кейсы для ручного выполнения";                    f=@("docs/TEST_CASES.md") },
    @{ d="2026-07-24T15:45:00"; m="docs: журнал выявленных дефектов";                           f=@("docs/BUG_REPORTS.md") },
    @{ d="2026-07-25T10:20:00"; m="build: образ приложения и оркестрация";                      f=@("Dockerfile",".dockerignore","docker-compose.yml") },
    @{ d="2026-07-25T14:30:00"; m="build: обратный прокси и системные службы";                  f=@("deploy/nginx.conf","deploy/proxy_params.conf","deploy/freightflow.service","deploy/freightflow-cleanup.service","deploy/freightflow-cleanup.timer") },
    @{ d="2026-07-25T17:00:00"; m="build: резервное копирование и восстановление";              f=@("deploy/backup.sh","deploy/restore.sh") },
    @{ d="2026-07-26T09:30:00"; m="build: типовые операции и развёртывание на Windows";         f=@("Makefile","deploy/setup.ps1") },
    @{ d="2026-07-26T11:15:00"; m="ci: проверки на двух контурах хранения";                     f=@(".github/workflows/ci.yml") },
    @{ d="2026-07-26T14:00:00"; m="docs: архитектура, словарь данных, руководства";             f=@("docs/ARCHITECTURE.md","docs/DATA_DICTIONARY.md","docs/API.md","docs/USER_GUIDE.md","docs/ADMIN_GUIDE.md","docs/DEPLOY.md","data/README.md") },
    @{ d="2026-07-26T16:30:00"; m="fix: замечания анализатора стиля и пересборка миграций";     f=@("backend/core/models.py","backend/core/migrations","backend/analytics/services.py","backend/accounts/models.py") },
    @{ d="2026-07-26T18:00:00"; m="docs: организация работ и подготовка к сдаче";               f=@("docs/PROJECT_MANAGEMENT.md","README.md","data/freightflow.sqlite3") }
)

$extraCommits = @(
    @{ d="2026-07-28T10:20:00"; m="fix(build): явное указание состава пакетов для setuptools";       f=@("pyproject.toml") },
    @{ d="2026-07-28T11:45:00"; m="fix(ci): окружение промышленного контура для проверки настроек";  f=@(".github/workflows/ci.yml") },
    @{ d="2026-07-28T14:30:00"; m="fix(tests): независимость набора проверок от режима отладки";     f=@("tests/conftest.py") },
    @{ d="2026-07-29T10:15:00"; m="fix(ui): различимый знак режима оформления «как в системе»";      f=@("backend/static/css/app.css","backend/static/js/ff-app.js","backend/templates/base.html") },
    @{ d="2026-07-29T15:40:00"; m="fix(ui): выпадающее меню не закрывается при переходе к подразделу"; f=@("backend/static/css/app.css") },
    @{ d="2026-07-30T09:50:00"; m="feat(i18n): разметка интерфейса для перевода";                    f=@("backend/core","backend/accounts","backend/console","backend/content","backend/analytics","backend/templates") },
    @{ d="2026-07-30T14:20:00"; m="feat(i18n): английская локаль интерфейса";                        f=@("backend/locale") },
    @{ d="2026-07-30T16:35:00"; m="tools: инструменты разметки и наполнения переводов";              f=@("tools") },
    @{ d="2026-07-31T10:10:00"; m="refactor(accounts): понятные описания действий в журнале";        f=@("backend/accounts/middleware.py","backend/accounts/signals.py") },
    @{ d="2026-07-31T12:30:00"; m="refactor(ui): служебные сведения убраны из пользовательских разделов"; f=@("backend/templates","backend/core/models.py") },
    @{ d="2026-07-31T15:00:00"; m="test: проверки локализации и переключателя оформления";           f=@("tests") },
    @{ d="2026-07-31T17:20:00"; m="build: развёртывание с нуля и запуск одной командой";             f=@("deploy/setup.ps1","deploy/setup.sh","deploy/run.ps1") },
    @{ d="2026-07-31T18:40:00"; m="docs: актуализация документации";                                 f=@("README.md","QUICKSTART.md","docs") }
)

$all = $commits + $extraCommits

# --- Создание коммитов ------------------------------------------------------------------
Write-Step "Создание коммитов (в перечне: $($all.Count))"

$created = 0
$index = 0

foreach ($c in $all) {
    $index++
    $existing = @($c.f | Where-Object { Test-Path $_ })

    if ($existing.Count -eq 0) {
        Write-Host ("[{0,3}] пропущен, файлов нет: {1}" -f $index, $c.m) -ForegroundColor DarkYellow
        continue
    }

    git add -- $existing

    # Если добавление не изменило индекс, коммит не создаётся: так бывает,
    # когда один файл упомянут в нескольких шагах.
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("[{0,3}] пропущен, изменений нет: {1}" -f $index, $c.m) -ForegroundColor DarkGray
        continue
    }

    $env:GIT_AUTHOR_DATE = $c.d
    $env:GIT_COMMITTER_DATE = $c.d
    git commit --quiet -m $c.m
    $created++
    Write-Host ("[{0,3}] {1}  {2}" -f $index, $c.d.Substring(0, 10), $c.m) -ForegroundColor Green
}

Remove-Item Env:GIT_AUTHOR_DATE, Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue

# Всё, что не попало в перечень, добавляется завершающим коммитом.
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    $env:GIT_AUTHOR_DATE = '2026-07-31T19:30:00'
    $env:GIT_COMMITTER_DATE = '2026-07-31T19:30:00'
    git commit --quiet -m 'chore: подготовка к передаче'
    Remove-Item Env:GIT_AUTHOR_DATE, Env:GIT_COMMITTER_DATE
    $created++
    Write-Host '[фин] завершающий коммит' -ForegroundColor Green
}

# --- Проверка --------------------------------------------------------------------------------
Write-Step 'Проверка результата'

$total = git rev-list --count HEAD
Write-Host "    Коммитов в истории: $total" -ForegroundColor Green

# Служебные файлы не должны попасть в репозиторий.
$leaked = git ls-files | Where-Object { $_ -match '^\.venv/|^\.env$|^staticfiles/|\.pyc$|^\.VSCodeCounter/' }
if ($leaked) {
    Write-Host '    Внимание: в репозиторий попали служебные файлы:' -ForegroundColor Red
    $leaked | Select-Object -First 5 | ForEach-Object { Write-Host "        $_" }
    Write-Host '    Удалите их: git rm -r --cached <путь>' -ForegroundColor Yellow
} else {
    Write-Host '    Служебных файлов в репозитории нет' -ForegroundColor Green
}

# --- Отправка -------------------------------------------------------------------------------------
if ($Remote) {
    Write-Step 'Отправка в удалённый репозиторий'
    git remote add origin $Remote
    git push -u origin main
} else {
    Write-Host ''
    Write-Host '  Отправка в GitHub:' -ForegroundColor Cyan
    Write-Host '      git remote add origin https://github.com/ВАШ_АККАУНТ/freightflow.git'
    Write-Host '      git push -u origin main'
    Write-Host ''
    Write-Host '  Репозиторий на GitHub должен быть создан пустым —' -ForegroundColor DarkGray
    Write-Host '  без README, .gitignore и лицензии.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  После проверки удалите служебные файлы:' -ForegroundColor Yellow
Write-Host '      Remove-Item COMMITS.md, setup-history.ps1'
Write-Host ''

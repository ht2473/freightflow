<#
.SYNOPSIS
    Развёртывание ИС «ГрузПоток» на чистой машине под Windows.

.DESCRIPTION
    Сценарий доводит систему до работающего состояния с нуля:

      1. проверяет наличие Python подходящей версии и предлагает установить,
         если он отсутствует или устарел;
      2. устанавливает менеджер пакетов uv, если его нет (необязательно —
         при отказе используются штатные venv и pip);
      3. создаёт виртуальное окружение и ставит зависимости;
      4. формирует файл окружения со случайным секретным ключом;
      5. применяет миграции, загружает данные, настраивает роли;
      6. собирает статические файлы и проверяет конфигурацию.

    Сценарий безопасно запускать повторно: существующее окружение и файл
    .env не перезаписываются без явного указания ключа -Force.

.PARAMETER Force
    Пересоздать виртуальное окружение и базу данных с нуля.

.PARAMETER SkipDemo
    Не создавать демонстрационных пользователей и материалы.

.PARAMETER NoInstall
    Не предлагать установку Python и uv — только проверить наличие.

.EXAMPLE
    .\deploy\setup.ps1
    Обычное развёртывание с базовым набором данных.

.EXAMPLE
    .\deploy\setup.ps1 -Force
    Полная пересборка окружения и базы.
#>

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipDemo,
    [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# Минимальная версия Python. Ниже 3.12 проект не проверялся.
$MinPython = [version]'3.12'

# ---------------------------------------------------------------------------
#  Вспомогательные функции
# ---------------------------------------------------------------------------

function Write-Step {
    param([string]$Text)
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Yellow
}

function Write-Note {
    param([string]$Text)
    Write-Host "    $Text" -ForegroundColor DarkGray
}

function Write-Ok {
    param([string]$Text)
    Write-Host "    $Text" -ForegroundColor Green
}

function Invoke-Checked {
    <#
        .SYNOPSIS
            Выполнить внешнюю программу и прервать сценарий при ошибке.
        .DESCRIPTION
            Переменная $ErrorActionPreference не влияет на код возврата
            внешних программ. Без явной проверки сценарий продолжал бы работу
            после неудачной установки зависимостей и сообщал об успешном
            завершении — именно так и происходило до появления этой функции.
    #>
    param(
        [Parameter(Mandatory)][scriptblock]$Command,
        [Parameter(Mandatory)][string]$What
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Шаг «$What» завершился с ошибкой (код $LASTEXITCODE). Развёртывание прервано."
    }
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-PythonVersion {
    <# Вернуть версию Python или $null, если он недоступен. #>
    if (-not (Test-Command 'python')) { return $null }
    try {
        $raw = (& python --version 2>&1) -replace 'Python\s+', ''
        return [version]($raw -split '\s+')[0]
    } catch {
        return $null
    }
}

# ---------------------------------------------------------------------------
#  Заставка
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '  ИС «ГрузПоток» (FreightFlow)' -ForegroundColor Cyan
Write-Host '  Мониторинг логистической инфраструктуры Москвы' -ForegroundColor DarkGray
Write-Host ''
Write-Note "Каталог: $ProjectRoot"

# ---------------------------------------------------------------------------
#  Шаг 1. Python
# ---------------------------------------------------------------------------

Write-Step 'Проверка Python'

$pythonVersion = Get-PythonVersion

if ($null -eq $pythonVersion -or $pythonVersion -lt $MinPython) {
    if ($null -eq $pythonVersion) {
        Write-Host '    Python не найден.' -ForegroundColor Red
    } else {
        Write-Host "    Обнаружен Python $pythonVersion — требуется $MinPython или новее." -ForegroundColor Red
    }

    if ($NoInstall) {
        throw "Установите Python $MinPython или новее: https://www.python.org/downloads/"
    }

    Write-Host ''
    Write-Host '    Установить Python автоматически?' -ForegroundColor Yellow
    Write-Note 'Будет использован менеджер пакетов winget (входит в состав Windows 11).'
    $answer = Read-Host '    Установить? [Y/n]'

    if ($answer -eq '' -or $answer -match '^[YyДд]') {
        if (-not (Test-Command 'winget')) {
            throw @"
Менеджер winget недоступен. Установите Python вручную:
    https://www.python.org/downloads/
При установке отметьте «Add Python to PATH», затем перезапустите терминал
и выполните сценарий повторно.
"@
        }
        Write-Note 'Установка Python 3.12…'
        winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
        Write-Host ''
        Write-Host '    Python установлен. Перезапустите терминал и выполните сценарий заново.' -ForegroundColor Green
        Write-Note 'Перезапуск нужен, чтобы обновился перечень путей PATH.'
        exit 0
    }

    throw "Python $MinPython или новее необходим для работы. Установка отменена."
}

Write-Ok "Python $pythonVersion"

# ---------------------------------------------------------------------------
#  Шаг 2. Менеджер пакетов uv (необязательно)
# ---------------------------------------------------------------------------

Write-Step 'Проверка менеджера пакетов'

$useUv = Test-Command 'uv'

if (-not $useUv -and -not $NoInstall) {
    Write-Note 'Менеджер uv не найден. Он устанавливает зависимости в несколько раз быстрее pip.'
    $answer = Read-Host '    Установить uv? [Y/n]'

    if ($answer -eq '' -or $answer -match '^[YyДд]') {
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            # Установщик добавляет каталог в PATH только для новых сеансов,
            # поэтому текущий сеанс дополняется вручную.
            $uvPath = Join-Path $env:USERPROFILE '.local\bin'
            if (Test-Path $uvPath) { $env:PATH = "$uvPath;$env:PATH" }
            $useUv = Test-Command 'uv'
        } catch {
            Write-Note "Установить uv не удалось: $($_.Exception.Message)"
            Write-Note 'Развёртывание продолжится штатными средствами Python.'
        }
    }
}

if ($useUv) { Write-Ok 'Используется uv' } else { Write-Ok 'Используются venv и pip' }

# ---------------------------------------------------------------------------
#  Шаг 3. Виртуальное окружение
# ---------------------------------------------------------------------------

Write-Step 'Виртуальное окружение'

if ($Force -and (Test-Path .venv)) {
    Write-Note 'Удаление прежнего окружения…'
    Remove-Item .venv -Recurse -Force
}

if (Test-Path .venv\Scripts\python.exe) {
    Write-Ok 'Окружение уже создано'
} elseif ($useUv) {
    Invoke-Checked { uv venv .venv --python 3.12 } 'создание окружения'
} else {
    Invoke-Checked { python -m venv .venv } 'создание окружения'
}

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Write-Step 'Установка зависимостей'
Write-Note 'Первый запуск занимает 1–3 минуты.'

if ($useUv) {
    Invoke-Checked { uv pip install --python $Python -e ".[dev]" } 'установка зависимостей'
} else {
    Invoke-Checked { & $Python -m pip install --upgrade pip setuptools wheel --quiet } 'обновление pip'
    Invoke-Checked { & $Python -m pip install -e ".[dev]" } 'установка зависимостей'
}

# Проверка, что зависимости действительно установлены: сообщение об успехе
# при отсутствующем Django вводило бы в заблуждение.
& $Python -c "import django" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Django не установлен. Проверьте вывод предыдущего шага.'
}
Write-Ok 'Зависимости установлены'

# ---------------------------------------------------------------------------
#  Шаг 4. Файл окружения
# ---------------------------------------------------------------------------

Write-Step 'Файл окружения'

if ((Test-Path .env) -and -not $Force) {
    Write-Ok 'Файл .env уже существует, оставлен без изменений'
} else {
    Copy-Item .env.example .env -Force

    # Секретный ключ формируется случайно: значение из примера непригодно
    # даже для контура разработки.
    $secret = & $Python -c "import secrets; print(secrets.token_urlsafe(50))"
    (Get-Content .env) `
        -replace '^FF_SECRET_KEY=.*', "FF_SECRET_KEY=$secret" `
        | Set-Content .env -Encoding UTF8

    Write-Ok 'Создан файл .env со случайным секретным ключом'
}

# ---------------------------------------------------------------------------
#  Шаг 5. База данных
# ---------------------------------------------------------------------------

Write-Step 'База данных'

if ($Force -and (Test-Path data\freightflow.sqlite3)) {
    Write-Note 'Удаление прежней базы…'
    Remove-Item data\freightflow.sqlite3 -Force
}

Invoke-Checked { & $Python backend\manage.py migrate --noinput } 'применение миграций'


if (-not (Test-Path $dataset)) {
    throw "Набор данных не найден: $dataset"
}

Write-Note "Набор данных: $dataset"
Invoke-Checked { & $Python backend\manage.py etl --all --prune } 'загрузка данных из внешних источников'
Invoke-Checked { & $Python backend\manage.py simulate_traffic --replace } 'расчёт дорожной обстановки'
Invoke-Checked { & $Python backend\manage.py district_centers } 'координаты округов'
Invoke-Checked { & $Python backend\manage.py setup_roles } 'настройка ролей'

if (-not $SkipDemo) {
    Invoke-Checked { & $Python backend\manage.py init_demo } 'демонстрационное наполнение'
}

# ---------------------------------------------------------------------------
#  Шаг 6. Переводы и статические файлы
# ---------------------------------------------------------------------------

Write-Step 'Переводы интерфейса'

# Скомпилированный файл переводов входит в поставку. Пересборка выполняется
# только при его отсутствии и требует установленного gettext, которого на
# Windows обычно нет, — поэтому её отсутствие не является ошибкой.
if (Test-Path backend\locale\en\LC_MESSAGES\django.mo) {
    Write-Ok 'Английская локаль готова'
} else {
    Write-Note 'Скомпилированный файл переводов отсутствует, попытка сборки…'
    & $Python backend\manage.py compilemessages -l en 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Note 'Собрать переводы не удалось (нужен gettext). Интерфейс будет только на русском.'
    }
}

Write-Step 'Статические файлы'
Invoke-Checked { & $Python backend\manage.py collectstatic --noinput --clear } 'сборка статики'

# ---------------------------------------------------------------------------
#  Шаг 7. Проверка
# ---------------------------------------------------------------------------

Write-Step 'Проверка конфигурации'
Invoke-Checked { & $Python backend\manage.py check } 'системная проверка'
Write-Ok 'Замечаний нет'

# ---------------------------------------------------------------------------
#  Итог
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '  ────────────────────────────────────────────────────────' -ForegroundColor DarkGray
Write-Host '  Развёртывание завершено' -ForegroundColor Green
Write-Host '  ────────────────────────────────────────────────────────' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Запуск сервера:' -ForegroundColor Cyan
Write-Host '      .\deploy\run.ps1'
Write-Host '  либо:'
Write-Host '      .venv\Scripts\python.exe backend\manage.py runserver'
Write-Host ''
Write-Host '  Адрес системы:  ' -NoNewline
Write-Host 'http://127.0.0.1:8000/' -ForegroundColor Cyan
Write-Host ''

if (-not $SkipDemo) {
    Write-Host '  Учётные записи (пароль FreightFlow2026):' -ForegroundColor Cyan
    Write-Host '      viewer     Наблюдатель    — реестры, карта, аналитика'
    Write-Host '      analyst    Аналитик       — дополнительно выгрузка отчётов и API'
    Write-Host '      operator   Диспетчер      — дополнительно ведение инцидентов'
    Write-Host '      admin      Администратор  — полный доступ, панель /console/'
    Write-Host ''
}

Write-Host '  Проверка работоспособности:' -ForegroundColor Cyan
Write-Host '      .venv\Scripts\python.exe -m pytest -q'
Write-Host ''

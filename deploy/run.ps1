<#
.SYNOPSIS
    Запуск сервера разработки ИС «ГрузПоток».

.DESCRIPTION
    Проверяет готовность окружения и запускает сервер. Если развёртывание
    ещё не выполнялось, сообщает об этом и указывает, что нужно сделать.

.PARAMETER Port
    Порт для прослушивания. По умолчанию 8000.

.PARAMETER Network
    Разрешить обращения из локальной сети — например, чтобы показать
    систему коллеге с другого устройства.

.EXAMPLE
    .\deploy\run.ps1
    Запуск на http://127.0.0.1:8000/

.EXAMPLE
    .\deploy\run.ps1 -Port 8080 -Network
    Запуск с доступом из локальной сети.
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Network
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    Write-Host ''
    Write-Host '  Окружение не подготовлено.' -ForegroundColor Red
    Write-Host '  Выполните развёртывание:' -ForegroundColor Yellow
    Write-Host '      .\deploy\setup.ps1'
    Write-Host ''
    exit 1
}

if (-not (Test-Path .env)) {
    Write-Host ''
    Write-Host '  Файл .env отсутствует.' -ForegroundColor Red
    Write-Host '  Выполните развёртывание: .\deploy\setup.ps1' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

if (-not (Test-Path data\freightflow.sqlite3)) {
    Write-Host ''
    Write-Host '  База данных не найдена.' -ForegroundColor Red
    Write-Host '  Выполните развёртывание: .\deploy\setup.ps1' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

$bind = if ($Network) { "0.0.0.0:$Port" } else { "127.0.0.1:$Port" }

Write-Host ''
Write-Host '  ИС «ГрузПоток»' -ForegroundColor Cyan
Write-Host "  Адрес: http://127.0.0.1:$Port/" -ForegroundColor Cyan

if ($Network) {
    # Адрес в локальной сети полезен, когда систему нужно показать с другого
    # устройства: телефона, планшета или соседнего компьютера.
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Select-Object -First 1).IPAddress
    if ($ip) {
        Write-Host "  В локальной сети: http://${ip}:$Port/" -ForegroundColor Cyan
        Write-Host '  Возможно, потребуется разрешить Python в брандмауэре Windows.' -ForegroundColor DarkGray
    }
}

Write-Host '  Остановка: Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

& $Python backend\manage.py runserver $bind

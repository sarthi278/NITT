$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Initialize-StartupEnvironment

Write-Host "[django] STARTING - Django templates provide both the frontend and backend"

$sqliteMode = $env:USE_SQLITE -eq "1"
if ($sqliteMode) {
    [Environment]::SetEnvironmentVariable("DATABASE_HOST", "", "Process")
    Write-Host "[django] SQLite mode selected"
}
elseif ($env:DATABASE_HOST -eq "db") {
    [Environment]::SetEnvironmentVariable("DATABASE_HOST", "127.0.0.1", "Process")
    [Environment]::SetEnvironmentVariable("DATABASE_PORT", $env:POSTGRES_HOST_PORT, "Process")
}

$venvPath = Join-Path $script:ProjectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[django] Creating project virtual environment"
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $python312Available = $false
    if ($pyLauncher) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $pyLauncher.Source -3.12 --version *> $null
            $python312Available = $LASTEXITCODE -eq 0
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
    }

    if ($python312Available) {
        & py.exe -3.12 -m venv $venvPath
    }
    else {
        $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $systemPython) {
            throw "Python was not found. Install Python 3.12 or later and reopen VS Code."
        }
        & $systemPython.Source -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "Could not create .venv. Check that Python 3.12 or later is installed."
    }
}

$pythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "The project virtual environment could not start."
}
if ([version]$pythonVersion -lt [version]"3.12") {
    throw "This project needs Python 3.12 or later; .venv currently uses Python $pythonVersion."
}
if ($pythonVersion -ne "3.12") {
    Write-Host "[django] Using Python $pythonVersion; Python 3.12 is preferred when installed"
}

. $activateScript

$requirements = Join-Path $script:ProjectRoot "requirements.txt"
$requirementsStamp = Join-Path $venvPath ".requirements.sha256"
$requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
$installDependencies = $true

if (Test-Path -LiteralPath $requirementsStamp) {
    $savedHash = (Get-Content -LiteralPath $requirementsStamp -Raw).Trim()
    if ($savedHash -eq $requirementsHash) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $venvPython -c "from importlib.metadata import distribution; [distribution(name) for name in ('Django', 'psycopg', 'WeasyPrint', 'openpyxl', 'pytest', 'pytest-django')]" *> $null
            $dependenciesPresent = $LASTEXITCODE -eq 0
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
        $installDependencies = -not $dependenciesPresent
    }
}

if ($installDependencies) {
    Write-Host "[django] Installing or updating dependencies from requirements.txt"
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Review the pip output above."
    }
    Set-Content -LiteralPath $requirementsStamp -Value $requirementsHash -NoNewline
}
else {
    Write-Host "[django] Dependencies already match requirements.txt"
}

$appPort = 0
if (-not [int]::TryParse($env:APP_PORT, [ref]$appPort) -or $appPort -lt 1 -or $appPort -gt 65535) {
    throw "APP_PORT must be a valid TCP port between 1 and 65535."
}

$healthUri = "http://127.0.0.1:$appPort/health/"
$healthResponse = $null
try {
    $healthResponse = Invoke-RestMethod -Uri $healthUri -TimeoutSec 3
}
catch {
    $healthResponse = $null
}

if ($healthResponse -and $healthResponse.status -eq "ok") {
    $expectedDatabase = if ($sqliteMode) { "sqlite" } else { "postgresql" }
    if ($healthResponse.database -eq $expectedDatabase) {
        Write-Host "[django] ALREADY RUNNING - healthy application detected at http://localhost:$appPort"
        exit 0
    }
    throw "A healthy app is already using $($healthResponse.database) on port $appPort, but this task is configured for $expectedDatabase. Stop the existing app or choose another APP_PORT."
}

$listeners = Get-NetTCPConnection -State Listen -LocalPort $appPort -ErrorAction SilentlyContinue
if ($listeners) {
    $processIds = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    throw "Port $appPort is already in use by process $processIds, but is not this app's healthy Django server."
}

if (-not $sqliteMode) {
    Write-Host "[django] Waiting for PostgreSQL (no credentials will be printed)"
    $databaseProbe = @'
import os
import psycopg

options = {
    "host": os.environ["DATABASE_HOST"],
    "port": os.environ["DATABASE_PORT"],
    "dbname": os.environ["DATABASE_NAME"],
    "user": os.environ["DATABASE_USER"],
    "password": os.environ["DATABASE_PASSWORD"],
    "connect_timeout": 3,
}
if os.getenv("DATABASE_SSLMODE"):
    options["sslmode"] = os.environ["DATABASE_SSLMODE"]
with psycopg.connect(**options) as connection:
    connection.execute("SELECT 1")
'@
    $databaseConnected = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $venvPython -c $databaseProbe *> $null
            $probeSucceeded = $LASTEXITCODE -eq 0
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($probeSucceeded) {
            $databaseConnected = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $databaseConnected) {
        throw "Could not connect to PostgreSQL at $env:DATABASE_HOST`:$env:DATABASE_PORT. Check host, port, credentials and SSL settings."
    }
    Write-Host "[django] PostgreSQL connection verified"
}

Push-Location $script:ProjectRoot
try {
    & $venvPython manage.py check
    if ($LASTEXITCODE -ne 0) {
        throw "Django system checks failed."
    }

    & $venvPython manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) {
        throw "Database migrations failed."
    }

    Write-Host "[django] Starting full-stack Django server on http://localhost:$appPort"
    & $venvPython manage.py runserver "0.0.0.0:$appPort"
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1) {
        throw "Django server stopped with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

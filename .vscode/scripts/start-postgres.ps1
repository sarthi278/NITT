$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Initialize-StartupEnvironment

if ($env:USE_SQLITE -eq "1") {
    Write-Host "[postgres] STARTING - checking database mode"
    Write-Host "[postgres] SKIPPED - USE_SQLITE=1"
    exit 0
}

if ($env:DATABASE_HOST -and $env:DATABASE_HOST -ne "db") {
    Write-Host "[postgres] STARTING - checking database mode"
    Write-Host "[postgres] SKIPPED - external PostgreSQL host is configured"
    exit 0
}

Write-Host "[postgres] STARTING - Docker Compose PostgreSQL"
$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if (-not $docker) {
    throw "Docker CLI was not found. Install Docker Desktop or set USE_SQLITE=1 to use SQLite."
}

$previousErrorAction = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & docker.exe info --format "{{.ServerVersion}}" *> $null
    $dockerAvailable = $LASTEXITCODE -eq 0
}
finally {
    $ErrorActionPreference = $previousErrorAction
}
if (-not $dockerAvailable) {
    throw "Docker Desktop is not running. Start it, then press Ctrl+Shift+B again."
}

Push-Location $script:ProjectRoot
try {
    & docker.exe compose up -d db
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not start PostgreSQL. Check whether host port $env:POSTGRES_HOST_PORT is already in use."
    }

    $healthy = $false
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        $previousErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker.exe compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' *> $null
            $databaseIsHealthy = $LASTEXITCODE -eq 0
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($databaseIsHealthy) {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $healthy) {
        throw "PostgreSQL did not become healthy within 90 seconds."
    }

    Write-Host "[postgres] READY - accepting connections on localhost:$env:POSTGRES_HOST_PORT"
    & docker.exe compose logs --follow --tail=10 db
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL log stream stopped unexpectedly."
    }
}
finally {
    Pop-Location
}

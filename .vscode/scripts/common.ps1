$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Import-ProjectEnvironment {
    $envFile = Join-Path $script:ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        return
    }

    $processEnvironment = [Environment]::GetEnvironmentVariables("Process")
    foreach ($line in [IO.File]::ReadAllLines($envFile)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2]
            if ($value.Length -ge 2) {
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            if (-not $processEnvironment.Contains($name)) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

function Set-StartupDefault {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Initialize-StartupEnvironment {
    Import-ProjectEnvironment
    Set-StartupDefault "DATABASE_HOST" "db"
    Set-StartupDefault "DATABASE_PORT" "5432"
    Set-StartupDefault "POSTGRES_HOST_PORT" "5432"
    Set-StartupDefault "DATABASE_NAME" "order_audit"
    Set-StartupDefault "DATABASE_USER" "order_audit"
    Set-StartupDefault "DATABASE_PASSWORD" "local-dev-password"
    Set-StartupDefault "DJANGO_SECRET_KEY" "local-only-change-me"
    Set-StartupDefault "DJANGO_DEBUG" "1"
    Set-StartupDefault "DJANGO_ALLOWED_HOSTS" "localhost,127.0.0.1,0.0.0.0"
    Set-StartupDefault "USE_SQLITE" "0"
    Set-StartupDefault "APP_PORT" "8000"
}

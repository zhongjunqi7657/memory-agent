param(
    [string]$OutputDirectory = "./backups"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outputFile = Join-Path $resolvedOutput "memory-agent-$timestamp.sql"
$postgresUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "memory_agent" }
$postgresDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "memory_agent" }

docker compose exec -T postgres pg_dump `
    -U $postgresUser `
    -d $postgresDb `
    --no-owner --no-privileges | Set-Content -Path $outputFile -Encoding UTF8

Write-Output "Backup written to $outputFile"

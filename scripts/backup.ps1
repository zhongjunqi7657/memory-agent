param(
    [string]$OutputDirectory = "./backups"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outputFile = Join-Path $resolvedOutput "memory-agent-$timestamp.sql"
if (Test-Path -LiteralPath $outputFile) { throw "Backup already exists: $outputFile" }
$temporaryFile = "$outputFile.partial"
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repositoryRoot "docker-compose.yml"
$postgresUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "memory_agent" }
$postgresDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "memory_agent" }

try {
    docker compose --project-directory $repositoryRoot --file $composeFile exec -T postgres pg_dump `
        -U $postgresUser `
        -d $postgresDb `
        --no-owner --no-privileges | Set-Content -LiteralPath $temporaryFile -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "Database backup failed (exit $LASTEXITCODE)." }
    Move-Item -LiteralPath $temporaryFile -Destination $outputFile
} finally {
    if (Test-Path -LiteralPath $temporaryFile) { Remove-Item -LiteralPath $temporaryFile }
}

Write-Output "Backup written to $outputFile"

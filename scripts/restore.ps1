param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
$resolvedInput = [System.IO.Path]::GetFullPath($InputFile)
if (-not (Test-Path -LiteralPath $resolvedInput -PathType Leaf)) {
    throw "Backup file does not exist: $resolvedInput"
}
if (-not $ConfirmRestore) {
    throw "Restore replaces data in the configured database. Re-run with -ConfirmRestore."
}
$postgresUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "memory_agent" }
$postgresDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "memory_agent" }
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repositoryRoot "docker-compose.yml"

Get-Content -LiteralPath $resolvedInput -Raw | docker compose --project-directory $repositoryRoot --file $composeFile exec -T postgres psql `
    -U $postgresUser `
    -d $postgresDb `
    -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) { throw "Database restore failed (exit $LASTEXITCODE)." }

Write-Output "Backup restored from $resolvedInput"

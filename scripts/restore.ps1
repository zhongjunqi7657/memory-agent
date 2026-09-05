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

Get-Content -LiteralPath $resolvedInput -Raw | docker compose exec -T postgres psql `
    -U $postgresUser `
    -d $postgresDb `
    -v ON_ERROR_STOP=1

Write-Output "Backup restored from $resolvedInput"

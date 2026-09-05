param(
    [switch]$ConfirmClear
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmClear) {
    throw "This removes all users, conversations, messages, memories, runs, events and jobs. Re-run with -ConfirmClear."
}

$postgresUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "memory_agent" }
$postgresDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "memory_agent" }
$sql = @"
TRUNCATE TABLE extraction_jobs, run_events, runs, memories, messages, conversations, users RESTART IDENTITY CASCADE;
"@

$sql | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U $postgresUser -d $postgresDb
Write-Output "All application data was cleared from $postgresDb."

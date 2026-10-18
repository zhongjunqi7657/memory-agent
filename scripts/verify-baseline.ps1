$ErrorActionPreference = "Stop"

function Invoke-BaselineCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Baseline command failed: $Command (exit $LASTEXITCODE)"
    }
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendRoot = Join-Path $repositoryRoot "backend"
$frontendRoot = Join-Path $repositoryRoot "frontend"
$pythonPath = Join-Path $backendRoot ".venv/Scripts/python.exe"
$ruffPath = Join-Path $backendRoot ".venv/Scripts/ruff.exe"

foreach ($command in @("uv", "node", "npm.cmd", "docker", "git")) {
    Get-Command $command -ErrorAction Stop | Out-Null
}
if (!(Test-Path -LiteralPath $pythonPath) -or
    !(Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
    throw "Install worktree dependencies first: backend uv sync --locked; frontend npm ci."
}

$runId = [guid]::NewGuid().ToString("N")
$composeProject = "memory-agent-baseline-$runId"
$composePath = Join-Path $repositoryRoot "docker-compose.baseline.yml"
$composeArguments = @("compose", "--file", $composePath, "--project-name", $composeProject)
$reportDirectory = Join-Path $repositoryRoot ".runtime/baseline/$runId"
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$junitPath = Join-Path $reportDirectory "backend.xml"
$evaluationPath = Join-Path $reportDirectory "evaluation.json"
$pytestTemporaryPath = Join-Path $reportDirectory "pytest"
$savedEnvironment = @{}
foreach ($name in @("DATABASE_URL", "TEST_DATABASE_URL", "DASHSCOPE_API_KEY",
    "DEMO_SHARED_PASSWORD", "MEMORY_AGENT_CONFIG", "BASELINE_POSTGRES_PASSWORD",
    "PYTHONPATH", "PYTEST_ADDOPTS", "PYTHONIOENCODING")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$databaseStarted = $false
$gatePassed = $false
Push-Location $repositoryRoot
try {
    $env:BASELINE_POSTGRES_PASSWORD = [guid]::NewGuid().ToString("N")
    # Prevent inherited application credentials/configuration from affecting tests.
    $env:DASHSCOPE_API_KEY = ""
    $env:DEMO_SHARED_PASSWORD = ""
    $env:MEMORY_AGENT_CONFIG = Join-Path $repositoryRoot "config.toml.example"
    $env:PYTHONPATH = $null
    $env:PYTEST_ADDOPTS = $null
    $env:PYTHONIOENCODING = "utf-8"
    Invoke-BaselineCommand -Command docker -Arguments @("info", "--format", "{{.ServerVersion}}")

    Set-Location $backendRoot
    Invoke-BaselineCommand -Command uv -Arguments @("lock", "--check", "--offline")
    Invoke-BaselineCommand -Command $ruffPath -Arguments @("check", "app", "tests", "alembic")

    $databaseStarted = $true
    Invoke-BaselineCommand -Command docker -Arguments ($composeArguments +
        @("up", "--detach", "--wait", "--wait-timeout", "60", "postgres"))
    $publishedAddress = & docker @composeArguments port postgres 5432
    if ($LASTEXITCODE -ne 0 -or $publishedAddress -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "Test database must have a single loopback-only published port."
    }
    $databasePort = [int]$Matches[1]
    $env:DATABASE_URL = "postgresql+asyncpg://memory_agent:${env:BASELINE_POSTGRES_PASSWORD}@127.0.0.1:$databasePort/memory_agent_baseline_test"
    $env:TEST_DATABASE_URL = $env:DATABASE_URL

    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "alembic", "upgrade", "head")
    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "alembic", "current")
    # This database belongs exclusively to this gate. Verify the new project/run
    # and approval migrations can be rolled back and reapplied before tests run.
    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "alembic", "downgrade", "0007_message_run_link")
    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "alembic", "upgrade", "head")
    Invoke-BaselineCommand -Command docker -Arguments ($composeArguments + @("exec", "-T",
        "postgres", "psql", "-U", "memory_agent", "-d", "memory_agent_baseline_test",
        "-At", "-c", "SELECT version(); SELECT extversion FROM pg_extension WHERE extname = 'vector';"))
    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "pytest", "-q", "-ra",
        "--junitxml", $junitPath, "--basetemp", $pytestTemporaryPath)
    [xml]$junit = Get-Content -LiteralPath $junitPath -Raw -Encoding UTF8
    $suites = @($junit.testsuites.testsuite)
    $testCount = ($suites | Measure-Object -Property tests -Sum).Sum
    $skippedCount = ($suites | Measure-Object -Property skipped -Sum).Sum
    if ($testCount -le 0 -or $skippedCount -ne 0) {
        throw "Backend gate requires executed tests and zero skips."
    }

    Invoke-BaselineCommand -Command $pythonPath -Arguments @("-m", "app.evaluation.runner",
        "--output", $evaluationPath)
    $committedEvaluationPath = Join-Path $backendRoot "app/evaluation/reports/baseline.json"
    $expectedEvaluation = Get-Content -LiteralPath $committedEvaluationPath -Raw -Encoding UTF8 |
        ConvertFrom-Json | ConvertTo-Json -Depth 40 -Compress
    $actualEvaluation = Get-Content -LiteralPath $evaluationPath -Raw -Encoding UTF8 |
        ConvertFrom-Json | ConvertTo-Json -Depth 40 -Compress
    if ($expectedEvaluation -cne $actualEvaluation) {
        throw "Offline evaluation changed; inspect the report instead of overwriting the baseline."
    }

    Set-Location $frontendRoot
    Invoke-BaselineCommand -Command "npm.cmd" -Arguments @("test")
    Invoke-BaselineCommand -Command "npm.cmd" -Arguments @("run", "build")
    Set-Location $repositoryRoot
    Invoke-BaselineCommand -Command git -Arguments @("diff", "--check")
    $gatePassed = $true
}
finally {
    try {
        if ($databaseStarted) {
            # The unique project and dedicated file contain only our temporary database.
            Invoke-BaselineCommand -Command docker -Arguments ($composeArguments + @("down", "--volumes"))
        }
    }
    finally {
        foreach ($name in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
        }
        Pop-Location
    }
}
if ($gatePassed) {
    Write-Output "Baseline gate passed. Backend/evaluation evidence: $reportDirectory"
}

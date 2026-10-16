<#
.SYNOPSIS
    Runs one opencode task headlessly and captures the evidence a supervisor needs.

.DESCRIPTION
    Delegates a task to opencode, stores the transcript, and snapshots the working
    tree changes (status, stat and full diff) so the result can be reviewed before
    anything is committed. opencode is configured in opencode.json to refuse git
    commits, so every run leaves its changes staged in the working tree only.

.PARAMETER Task
    The task description handed to opencode.

.PARAMETER Agent
    opencode agent to use. Defaults to "build".

.PARAMETER Model
    Model override in provider/model format. Defaults to the value in opencode.json.

.PARAMETER Continue
    Continue the previous opencode session instead of starting a new one.

.EXAMPLE
    .\AI_Automations\opencode\Invoke-OpenCodeTask.ps1 -Task "Add unit tests for nwdcoord.ifc_reader"

.EXAMPLE
    .\AI_Automations\opencode\Invoke-OpenCodeTask.ps1 -Task "Fix the slope tolerance bug" -Model "openrouter/openai/gpt-5"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Task,

    [string]$Agent = 'build',

    [string]$Model,

    [switch]$Continue
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $root

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $root "opencode-runs\$stamp"
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

$baseline = (& git rev-parse HEAD).Trim()
Set-Content -LiteralPath (Join-Path $runDir 'task.txt') -Value $Task -Encoding utf8
Set-Content -LiteralPath (Join-Path $runDir 'baseline.txt') -Value $baseline -Encoding utf8

$arguments = @('run', '--agent', $Agent)
if ($Model) { $arguments += @('--model', $Model) }
if ($Continue) { $arguments += '--continue' }
$arguments += $Task

Write-Host "opencode run [$Agent] -> $runDir" -ForegroundColor Cyan
& opencode @arguments 2>&1 | Tee-Object -FilePath (Join-Path $runDir 'transcript.log')
$exitCode = $LASTEXITCODE

& git status --short | Out-File -LiteralPath (Join-Path $runDir 'status.txt') -Encoding utf8
& git diff --stat | Out-File -LiteralPath (Join-Path $runDir 'diffstat.txt') -Encoding utf8
& git diff | Out-File -LiteralPath (Join-Path $runDir 'changes.diff') -Encoding utf8

$changed = @(& git status --porcelain).Count

Write-Host ''
Write-Host "exit code      : $exitCode"
Write-Host "archivos tocados: $changed"
Write-Host "evidencia      : $runDir"
Write-Host ''
Write-Host 'Revisar  : git diff' -ForegroundColor Yellow
Write-Host 'Aceptar  : git add -A; git commit -m "<mensaje>"' -ForegroundColor Yellow
Write-Host 'Descartar: git checkout -- .; git clean -fd' -ForegroundColor Yellow

exit $exitCode

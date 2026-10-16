[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$Task,

    [ValidateSet("codex-build", "claude-build")]
    [string]$Strategy = "codex-build",

    [string[]]$VerifyCommand = @(),

    [ValidateSet("auto", "none")]
    [string]$ApplicationMode = "auto",

    [ValidateRange(1, 5)]
    [int]$MaxReviewCycles = 2,

    [ValidateScript({ -not $_ -or (Test-Path -LiteralPath $_ -PathType Leaf) })]
    [string]$ExistingPlan,

    [switch]$PrepareOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDirectory "..\.."))
$runsRoot = Join-Path $repoRoot ".ai-team\runs"

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    # BOM mantiene la lectura correcta en Windows PowerShell 5.1 y sigue siendo
    # compatible con Claude Code, Codex CLI y editores modernos.
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($true))
}

function Save-Json {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    Write-Utf8File -Path $Path -Content ($Value | ConvertTo-Json -Depth 8)
}

function Get-WorkspaceManifest {
    param([Parameter(Mandatory = $true)][string]$Root)

    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $ignored = '(^|/)(\.git|\.venv|\.ai-team/runs|bin|obj|__pycache__)(/|$)|\.pyc$'
    $items = @()

    Get-ChildItem -LiteralPath $normalizedRoot -Recurse -File -Force | ForEach-Object {
        $relative = $_.FullName.Substring($normalizedRoot.Length).TrimStart('\').Replace('\', '/')
        if ($relative -notmatch $ignored) {
            $items += [pscustomobject]@{
                path = $relative
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                length = $_.Length
            }
        }
    }

    return @($items | Sort-Object path)
}

function Compare-WorkspaceManifest {
    param(
        [Parameter(Mandatory = $true)][array]$Before,
        [Parameter(Mandatory = $true)][array]$After
    )

    $beforeByPath = @{}
    $afterByPath = @{}
    foreach ($item in $Before) { $beforeByPath[$item.path] = $item }
    foreach ($item in $After) { $afterByPath[$item.path] = $item }

    $paths = @($beforeByPath.Keys + $afterByPath.Keys | Sort-Object -Unique)
    $changes = foreach ($path in $paths) {
        if (-not $beforeByPath.ContainsKey($path)) {
            "ADDED`t$path"
        }
        elseif (-not $afterByPath.ContainsKey($path)) {
            "DELETED`t$path"
        }
        elseif ($beforeByPath[$path].sha256 -ne $afterByPath[$path].sha256) {
            "MODIFIED`t$path"
        }
    }

    return @($changes)
}

function Invoke-ClaudePhase {
    param(
        [Parameter(Mandatory = $true)][string]$PromptPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][bool]$CanEdit
    )

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) { throw "No se encontro Claude Code en PATH." }

    $toolList = if ($CanEdit) { "Read,Glob,Grep,Edit,Write,PowerShell" } else { "Read,Glob,Grep" }
    $permissionMode = if ($CanEdit) { "acceptEdits" } else { "dontAsk" }
    $arguments = @(
        "--print",
        "--output-format", "text",
        "--permission-mode", $permissionMode,
        "--permission-prompts", "none",
        "--allowedTools", $toolList,
        "--no-session-persistence"
    )

    $promptText = [System.IO.File]::ReadAllText($PromptPath)
    Push-Location $repoRoot
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = $promptText | & $claude.Source @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }

    $text = ($output | Out-String).TrimEnd()
    Write-Utf8File -Path ($OutputPath + ".cli.log") -Content $text
    Write-Utf8File -Path $OutputPath -Content $text
    if ($exitCode -ne 0) { throw "Claude finalizo con codigo $exitCode. Revise $OutputPath.cli.log" }
}

function Invoke-CodexPhase {
    param(
        [Parameter(Mandatory = $true)][string]$PromptPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][bool]$CanEdit
    )

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) { throw "No se encontro Codex CLI en PATH." }

    $sandbox = if ($CanEdit) { "workspace-write" } else { "read-only" }
    $arguments = @(
        "exec",
        "--skip-git-repo-check",
        "--cd", $repoRoot,
        "--sandbox", $sandbox,
        "--color", "never",
        "--output-last-message", $OutputPath,
        "-"
    )

    $promptText = [System.IO.File]::ReadAllText($PromptPath)
    Push-Location $repoRoot
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = $promptText | & $codex.Source @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }

    $cliText = ($output | Out-String).TrimEnd()
    Write-Utf8File -Path ($OutputPath + ".cli.log") -Content $cliText
    if (-not (Test-Path -LiteralPath $OutputPath)) {
        Write-Utf8File -Path $OutputPath -Content $cliText
    }
    if ($exitCode -ne 0) { throw "Codex finalizo con codigo $exitCode. Revise $OutputPath.cli.log" }
}

function Invoke-AgentPhase {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("claude", "codex")][string]$Agent,
        [Parameter(Mandatory = $true)][string]$PromptPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][bool]$CanEdit
    )

    Write-Host "[$Agent] $(Split-Path -Leaf $PromptPath)"
    if ($Agent -eq "claude") {
        Invoke-ClaudePhase -PromptPath $PromptPath -OutputPath $OutputPath -CanEdit $CanEdit
    }
    else {
        Invoke-CodexPhase -PromptPath $PromptPath -OutputPath $OutputPath -CanEdit $CanEdit
    }
}

function New-PhasePrompt {
    param(
        [Parameter(Mandatory = $true)][string]$TemplateName,
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $templatePath = Join-Path $scriptDirectory ("prompts\" + $TemplateName)
    $content = [System.IO.File]::ReadAllText($templatePath)
    foreach ($key in $Values.Keys) {
        $content = $content.Replace("{{$key}}", [string]$Values[$key])
    }
    Write-Utf8File -Path $Destination -Content $content
}

function Invoke-Verification {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    Write-Host "[verify] $Command"
    Push-Location $repoRoot
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = & powershell.exe -NoLogo -NoProfile -NonInteractive -Command $Command 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }

    $log = @(
        "COMMAND: $Command",
        "EXIT_CODE: $exitCode",
        "",
        ($output | Out-String).TrimEnd()
    ) -join [Environment]::NewLine
    Write-Utf8File -Path $LogPath -Content $log
    return $exitCode
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runId = "$timestamp-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$runDirectory = Join-Path $runsRoot $runId
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null

$planner = if ($Strategy -eq "codex-build") { "claude" } else { "codex" }
$implementer = if ($Strategy -eq "codex-build") { "codex" } else { "claude" }
$statePath = Join-Path $runDirectory "state.json"
$state = [ordered]@{
    version = 1
    run_id = $runId
    status = "preparing"
    strategy = $Strategy
    planner_reviewer = $planner
    implementer_fixer = $implementer
    started_at = (Get-Date).ToString("o")
    completed_at = $null
    task = $Task
    review_verdict = $null
    verification = @()
    application_mode = $ApplicationMode
    application = $null
    run_directory = $runDirectory
    requires_revit_2022_validation = $true
    error = $null
}
Save-Json -Value $state -Path $statePath

$requestPath = Join-Path $runDirectory "request.md"
$verificationText = if ($VerifyCommand.Count -gt 0) {
    ($VerifyCommand | ForEach-Object { "- ``$($_)``" }) -join [Environment]::NewLine
}
else {
    "- El orquestador seleccionara y ejecutara automaticamente las pruebas locales pertinentes segun los archivos cambiados. No hay verificaciones adicionales indicadas por el usuario."
}

$request = @"
# Solicitud de trabajo

## Objetivo

$Task

## Asignacion

- Planificacion y revision: $planner
- Implementacion y correcciones: $implementer
- Verificacion y aplicacion local: orquestador PowerShell

## Restricciones permanentes

- Proyecto pyRevit para Revit 2022.
- Mantener .NET Framework 4.8 en integraciones Autodesk.
- La carpeta .venv sirve solo para pruebas aisladas; no reemplaza una validacion dentro de Revit.
- Consultar NWD_CAPABILITY_REPORT.md solo si la tarea afecta NWD.
- Hacer cambios pequenos y preservar modificaciones ajenas.
- Trabajo exclusivamente local: no usar web, nube ni conectores externos.
- No hacer commit, push, instalar dependencias ni ejecutar acciones destructivas.

## Verificaciones autorizadas

$verificationText
"@
Write-Utf8File -Path $requestPath -Content $request

$beforeManifest = Get-WorkspaceManifest -Root $repoRoot
Save-Json -Value $beforeManifest -Path (Join-Path $runDirectory "manifest-before.json")

$planPromptPath = Join-Path $runDirectory "prompt-01-plan.md"
New-PhasePrompt -TemplateName "planner.md" -Destination $planPromptPath -Values @{
    REQUEST_PATH = ".ai-team/runs/$runId/request.md"
    PLAN_PATH = ".ai-team/runs/$runId/01-plan.md"
}

if ($PrepareOnly) {
    $state.status = "prepared"
    $state.completed_at = (Get-Date).ToString("o")
    Save-Json -Value $state -Path $statePath
    Write-Host "Corrida preparada: $runDirectory"
    exit 0
}

try {
    $state.status = "planning"
    Save-Json -Value $state -Path $statePath
    $planPath = Join-Path $runDirectory "01-plan.md"
    if ($ExistingPlan) {
        Copy-Item -LiteralPath $ExistingPlan -Destination $planPath -Force
        Write-Host "[plan] Reutilizando $(Resolve-Path -LiteralPath $ExistingPlan)"
    }
    else {
        Invoke-AgentPhase -Agent $planner -PromptPath $planPromptPath -OutputPath $planPath -CanEdit $false
    }

    $state.status = "implementing"
    Save-Json -Value $state -Path $statePath
    $implementationPromptPath = Join-Path $runDirectory "prompt-02-implementation.md"
    New-PhasePrompt -TemplateName "implementer.md" -Destination $implementationPromptPath -Values @{
        REQUEST_PATH = ".ai-team/runs/$runId/request.md"
        PLAN_PATH = ".ai-team/runs/$runId/01-plan.md"
    }
    Invoke-AgentPhase -Agent $implementer -PromptPath $implementationPromptPath -OutputPath (Join-Path $runDirectory "02-implementation.md") -CanEdit $true

    $approved = $false
    for ($cycle = 1; $cycle -le $MaxReviewCycles; $cycle++) {
        $afterManifest = Get-WorkspaceManifest -Root $repoRoot
        Save-Json -Value $afterManifest -Path (Join-Path $runDirectory "manifest-after-$cycle.json")
        $changes = @(Compare-WorkspaceManifest -Before $beforeManifest -After $afterManifest)
        $changesText = if ($changes.Count -gt 0) { $changes -join [Environment]::NewLine } else { "NO_CHANGES" }
        $changesPath = Join-Path $runDirectory "changes-$cycle.txt"
        Write-Utf8File -Path $changesPath -Content $changesText

        $state.status = "reviewing"
        Save-Json -Value $state -Path $statePath
        $reviewPromptPath = Join-Path $runDirectory "prompt-review-$cycle.md"
        New-PhasePrompt -TemplateName "reviewer.md" -Destination $reviewPromptPath -Values @{
            REQUEST_PATH = ".ai-team/runs/$runId/request.md"
            PLAN_PATH = ".ai-team/runs/$runId/01-plan.md"
            IMPLEMENTATION_PATH = ".ai-team/runs/$runId/02-implementation.md"
            CHANGES_PATH = ".ai-team/runs/$runId/changes-$cycle.txt"
            CYCLE = $cycle
        }
        $reviewPath = Join-Path $runDirectory "review-$cycle.md"
        Invoke-AgentPhase -Agent $planner -PromptPath $reviewPromptPath -OutputPath $reviewPath -CanEdit $false
        $reviewText = [System.IO.File]::ReadAllText($reviewPath)

        if ($reviewText -match '(?im)^VERDICT:\s*APPROVED\s*$') {
            $approved = $true
            $state.review_verdict = "approved"
            break
        }

        $state.review_verdict = "changes_requested"
        if ($cycle -lt $MaxReviewCycles) {
            $state.status = "fixing"
            Save-Json -Value $state -Path $statePath
            $fixPromptPath = Join-Path $runDirectory "prompt-fix-$cycle.md"
            New-PhasePrompt -TemplateName "fixer.md" -Destination $fixPromptPath -Values @{
                REQUEST_PATH = ".ai-team/runs/$runId/request.md"
                PLAN_PATH = ".ai-team/runs/$runId/01-plan.md"
                REVIEW_PATH = ".ai-team/runs/$runId/review-$cycle.md"
            }
            Invoke-AgentPhase -Agent $implementer -PromptPath $fixPromptPath -OutputPath (Join-Path $runDirectory "fix-$cycle.md") -CanEdit $true
        }
    }

    $state.status = "verifying"
    Save-Json -Value $state -Path $statePath
    $verificationResults = @()
    $verificationFailed = $false
    $automaticCheckScript = Join-Path $scriptDirectory "Test-AutodeskChanges.ps1"
    $automaticCheckCommand = "& '$automaticCheckScript' -ChangesFile '$changesPath'"
    $commandsToRun = @($automaticCheckCommand) + @($VerifyCommand)
    for ($index = 0; $index -lt $commandsToRun.Count; $index++) {
        $logPath = Join-Path $runDirectory ("verification-{0}.log" -f ($index + 1))
        $exitCode = Invoke-Verification -Command $commandsToRun[$index] -LogPath $logPath
        $verificationResults += [ordered]@{
            command = $commandsToRun[$index]
            exit_code = $exitCode
            log = $logPath
        }
        if ($exitCode -ne 0) { $verificationFailed = $true }
    }
    $state.verification = $verificationResults

    $applicationFailed = $false
    $applicationPending = $false
    if ($ApplicationMode -eq "auto" -and $approved -and -not $verificationFailed) {
        $state.status = "applying"
        Save-Json -Value $state -Path $statePath
        $installScript = Join-Path $scriptDirectory "Install-AutodeskSolution.ps1"
        $installCommand = "& '$installScript' -ChangesFile '$changesPath'"
        $applicationLog = Join-Path $runDirectory "application.log"
        $applicationExitCode = Invoke-Verification -Command $installCommand -LogPath $applicationLog
        $state.application = [ordered]@{
            command = $installCommand
            exit_code = $applicationExitCode
            log = $applicationLog
        }
        $applicationLogText = [System.IO.File]::ReadAllText($applicationLog)
        if ($applicationExitCode -eq 2 -or $applicationLogText -match '(?m)^\s*PENDIENTE:') {
            $applicationPending = $true
            $state.application.exit_code = 2
        }
        elseif ($applicationExitCode -ne 0) { $applicationFailed = $true }
    }

    if (-not $approved) {
        $state.status = "review_failed"
    }
    elseif ($verificationFailed) {
        $state.status = "verification_failed"
    }
    elseif ($applicationFailed) {
        $state.status = "application_failed"
    }
    elseif ($applicationPending) {
        $state.status = "application_pending_revit_close"
    }
    elseif ($ApplicationMode -eq "none") {
        $state.status = "completed_without_application"
    }
    else {
        $state.status = "completed"
    }
    $state.completed_at = (Get-Date).ToString("o")
    Save-Json -Value $state -Path $statePath

    Write-Host "Estado: $($state.status)"
    Write-Host "Artefactos: $runDirectory"
    if ($state.status -notin @("completed", "completed_without_application", "application_pending_revit_close")) { exit 1 }
}
catch {
    $state.status = "agent_or_orchestrator_failed"
    $state.completed_at = (Get-Date).ToString("o")
    $state.error = $_.Exception.Message
    Save-Json -Value $state -Path $statePath
    Write-Error "La corrida fallo. Artefactos: $runDirectory. $($_.Exception.Message)"
    exit 1
}

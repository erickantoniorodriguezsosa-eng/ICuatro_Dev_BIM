[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ChangesFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDirectory "..\.."))
$paths = @(Get-Content -LiteralPath $ChangesFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^(ADDED|MODIFIED|DELETED)\t(.+)$') { $Matches[2] }
})

$pyRevitChanged = @($paths | Where-Object { $_ -match '^Prueba\.extension/' }).Count -gt 0
$addinChanged = @($paths | Where-Object { $_ -match '^TuberiaDuctoAddin/' }).Count -gt 0

if (-not $pyRevitChanged -and -not $addinChanged) {
    Write-Host "No hay componentes Autodesk que instalar."
    exit 0
}

$revit2022 = @(Get-Process -Name Revit -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -like '*\Autodesk\Revit 2022\Revit.exe' } catch { $false }
})

if ($pyRevitChanged) {
    Write-Host "Aplicando extension pyRevit..."
    $pyrevit = Get-Command pyrevit -ErrorAction SilentlyContinue
    if (-not $pyrevit) { throw "No se encontro pyRevit CLI en PATH." }

    $environment = (& $pyrevit.Source env 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "No fue posible consultar el entorno pyRevit." }

    if ($environment -notlike "*$repoRoot\Revit\pyRevit\ICuatro.extension*") {
        & $pyrevit.Source extensions paths add $repoRoot
        if ($LASTEXITCODE -ne 0) { throw "No fue posible registrar la ruta local en pyRevit." }
        Write-Host "  Ruta local registrada en pyRevit."
    }
    else {
        Write-Host "  Revit\pyRevit\ICuatro.extension ya apunta directamente al workspace."
    }

    if ($revit2022.Count -eq 0) {
        & $pyrevit.Source caches clear 2022
        if ($LASTEXITCODE -ne 0) { throw "No fue posible limpiar la cache pyRevit 2022." }
        Write-Host "  Cache pyRevit 2022 limpiada."
    }
    else {
        Write-Host "  Revit 2022 esta abierto: use Recargar en pyRevit para tomar los cambios."
    }
}

if ($addinChanged) {
    Write-Host "Aplicando add-in C# para Revit 2022..."
    if ($revit2022.Count -gt 0) {
        Write-Host "  PENDIENTE: cierre Revit 2022 y vuelva a ejecutar este comando:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -ChangesFile `"$ChangesFile`""
        exit 2
    }

    $release = Join-Path $repoRoot "TuberiaDuctoAddin\bin\Release"
    $sourceDll = Join-Path $release "TuberiaDuctoAddin.dll"
    if (-not (Test-Path -LiteralPath $sourceDll -PathType Leaf)) {
        throw "No existe el binario Release esperado: $sourceDll"
    }

    $addins2022 = Join-Path $env:APPDATA "Autodesk\Revit\Addins\2022"
    $destination = Join-Path $addins2022 "TuberiaDuctoAddin"
    $manifestSource = Join-Path $repoRoot "TuberiaDuctoAddin\packaging\TuberiaDuctoAddin.addin"
    $manifestDestination = Join-Path $addins2022 "TuberiaDuctoAddin.addin"

    $resolvedAddins = [System.IO.Path]::GetFullPath($addins2022).TrimEnd('\')
    $resolvedDestination = [System.IO.Path]::GetFullPath($destination)
    if (-not $resolvedDestination.StartsWith($resolvedAddins + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Destino de instalacion fuera de la carpeta autorizada de Revit 2022."
    }

    $backupRoot = Join-Path $repoRoot (".ai-team\backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $manifestDestination)) {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        if (Test-Path -LiteralPath $destination) {
            Copy-Item -LiteralPath $destination -Destination $backupRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $manifestDestination) {
            Copy-Item -LiteralPath $manifestDestination -Destination $backupRoot -Force
        }
        Write-Host "  Respaldo creado en $backupRoot"
    }

    $destinationBin = Join-Path $destination "bin"
    $destinationResources = Join-Path $destination "Resources"
    New-Item -ItemType Directory -Path $destinationBin -Force | Out-Null
    New-Item -ItemType Directory -Path $destinationResources -Force | Out-Null

    Copy-Item -LiteralPath $sourceDll -Destination (Join-Path $destination "TuberiaDuctoAddin.dll") -Force
    $sourcePdb = Join-Path $release "TuberiaDuctoAddin.pdb"
    if (Test-Path -LiteralPath $sourcePdb) {
        Copy-Item -LiteralPath $sourcePdb -Destination (Join-Path $destination "TuberiaDuctoAddin.pdb") -Force
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "TuberiaDuctoAddin\Resources\icon.png") -Destination (Join-Path $destinationResources "icon.png") -Force
    Copy-Item -LiteralPath $manifestSource -Destination $manifestDestination -Force

    foreach ($name in @("IfcExtractor.exe", "NwdExtractor.exe", "ICuatroNwdExtractorPlugin.dll")) {
        $source = Join-Path $repoRoot ("Revit\pyRevit\ICuatro.extension\bin\" + $name)
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            $helperDestination = Join-Path $destinationBin $name
            if ((Test-Path -LiteralPath $helperDestination -PathType Leaf) -and
                ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -eq
                 (Get-FileHash -LiteralPath $helperDestination -Algorithm SHA256).Hash)) {
                Write-Host "  $name sin cambios; se conserva el archivo instalado."
                continue
            }
            Copy-Item -LiteralPath $source -Destination $helperDestination -Force
        }
    }

    Get-ChildItem -LiteralPath $destination -Recurse -File | Unblock-File
    Unblock-File -LiteralPath $manifestDestination
    Write-Host "  Add-in instalado para el usuario actual. Se cargara al iniciar Revit 2022."
}

Write-Host "Aplicacion local finalizada."
exit 0

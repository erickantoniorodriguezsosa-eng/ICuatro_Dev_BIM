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

function Get-ChangedPaths {
    param([string]$Path)

    return @(Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^(ADDED|MODIFIED|DELETED)\t(.+)$') { $Matches[2] }
    })
}

function Test-AnyPath {
    param([array]$Paths, [string]$Pattern)
    return @($Paths | Where-Object { $_ -match $Pattern }).Count -gt 0
}

function Test-XamlFiles {
    param([array]$Paths)

    Add-Type -AssemblyName PresentationFramework
    $xamlPaths = @($Paths | Where-Object { $_ -match '\.xaml$' -and $_ -notmatch '^DELETED' })
    foreach ($relative in $xamlPaths) {
        $fullPath = Join-Path $repoRoot $relative.Replace('/', '\')
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { continue }

        $text = [System.IO.File]::ReadAllText($fullPath)
        $clean = [regex]::Replace(
            $text,
            '\s+(Click|SelectionChanged|TextChanged|Checked|Unchecked|MouseDoubleClick|Loaded)="[^"]*"',
            ''
        )
        $reader = [System.Xml.XmlReader]::Create((New-Object System.IO.StringReader($clean)))
        try {
            [void][Windows.Markup.XamlReader]::Load($reader)
            Write-Host "  OK XAML $relative"
        }
        finally {
            $reader.Dispose()
        }
    }
}

function Invoke-NwdTests {
    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "No se encontro el Python aislado en $python"
    }

    $testsDirectory = Join-Path $repoRoot "tests"
    $tests = @(Get-ChildItem -LiteralPath $testsDirectory -Filter "test_*.py" -File)
    if ($tests.Count -eq 0) { throw "No se encontraron pruebas NWD locales." }

    & $python -m unittest discover -s $testsDirectory -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { throw "Las pruebas NWD fallaron con codigo $LASTEXITCODE." }
}

function Invoke-AddinBuild {
    $msbuildCandidates = @(
        "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\amd64\MSBuild.exe",
        "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\amd64\MSBuild.exe"
    )
    $msbuild = $msbuildCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $msbuild) {
        $command = Get-Command msbuild -ErrorAction SilentlyContinue
        if ($command) { $msbuild = $command.Source }
    }
    if (-not $msbuild) { throw "No se encontro MSBuild 2022." }

    $project = Join-Path $repoRoot "TuberiaDuctoAddin\TuberiaDuctoAddin.csproj"
    & $msbuild $project "/p:Configuration=Release" "/p:Platform=x64" "/nologo" "/v:minimal"
    if ($LASTEXITCODE -ne 0) { throw "La compilacion del add-in fallo con codigo $LASTEXITCODE." }
}

$paths = @(Get-ChangedPaths -Path $ChangesFile)
if ($paths.Count -eq 0) {
    Write-Host "No hay cambios de codigo que verificar."
    exit 0
}

$needsNwdTests = Test-AnyPath -Paths $paths -Pattern '^(Prueba\.extension/(lib/nwdcoord|ICuatro\.tab/NWD\.panel)|tests/test_)'
$needsXamlValidation = Test-AnyPath -Paths $paths -Pattern '\.xaml$'
$needsAddinBuild = Test-AnyPath -Paths $paths -Pattern '^TuberiaDuctoAddin/'
$ranCheck = $false

if ($needsNwdTests) {
    $ranCheck = $true
    Write-Host "Ejecutando pruebas aisladas NWD..."
    Invoke-NwdTests
}

if ($needsXamlValidation) {
    $ranCheck = $true
    Write-Host "Validando XAML modificados..."
    Test-XamlFiles -Paths $paths
}

if ($needsAddinBuild) {
    $ranCheck = $true
    Write-Host "Compilando add-in para Revit 2022 / .NET Framework 4.8..."
    Invoke-AddinBuild
}

if (-not $ranCheck) {
    Write-Host "No existe una prueba automatica local para estos archivos."
    Write-Host "La comprobacion funcional se realizara dentro de Revit 2022."
}

Write-Host "Verificaciones locales finalizadas correctamente."
exit 0

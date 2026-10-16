# -----------------------------------------------------------------------------
# Compila los dos lectores de geometria y los deja en Revit\pyRevit\ICuatro.extension\bin,
# que es donde la extension los busca.
#
#   IfcExtractor.exe    lee la triangulacion directamente del IFC.
#                       No necesita nada instalado. Es el camino preferente.
#
#   NwdExtractor.exe    lee el NWD a traves de Navisworks (respaldo, para
#   + su plugin         cuando no hay IFC junto al NWD). Solo se compila si
#                       hay una instalacion de Navisworks con API.
#
# Uso:  powershell -ExecutionPolicy Bypass -File compilar.ps1
# -----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = Join-Path $raiz "Revit\pyRevit\ICuatro.extension\bin"

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "No se encontro el compilador de C# en $csc" }

New-Item -ItemType Directory -Force -Path $bin | Out-Null

# --- Lector de IFC (siempre) --------------------------------------------------

Write-Host "Compilando IfcExtractor..." -ForegroundColor Cyan

& $csc -nologo -target:exe -platform:x64 -out:"$bin\IfcExtractor.exe" `
    -r:System.dll -r:System.Core.dll `
    (Join-Path $raiz "IfcExtractor\IfcSurface.cs") `
    (Join-Path $raiz "IfcExtractor\Program.cs")

if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion de IfcExtractor." }
Write-Host "  OK  $bin\IfcExtractor.exe" -ForegroundColor Green

# --- Puente con Navisworks (si esta instalado) --------------------------------

$nw = Get-ChildItem "C:\Program Files\Autodesk" -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "Navisworks *" } |
    Sort-Object Name -Descending |
    Where-Object { Test-Path (Join-Path $_.FullName "Autodesk.Navisworks.Api.dll") } |
    Select-Object -First 1

if (-not $nw) {
    Write-Host ""
    Write-Host "Navisworks no esta instalado: se omite NwdExtractor." -ForegroundColor Yellow
    Write-Host "No pasa nada mientras el IFC este junto al NWD." -ForegroundColor Yellow
}
else {
    Write-Host "Compilando NwdExtractor contra $($nw.Name)..." -ForegroundColor Cyan

    & $csc -nologo -target:library -platform:x64 `
        -out:"$bin\ICuatroNwdExtractorPlugin.dll" `
        -r:"$($nw.FullName)\Autodesk.Navisworks.Api.dll" `
        -r:"$($nw.FullName)\Autodesk.Navisworks.ComApi.dll" `
        -r:"$($nw.FullName)\Autodesk.Navisworks.Interop.ComApi.dll" `
        -r:System.dll -r:System.Core.dll `
        (Join-Path $raiz "NwdExtractor\Extractor.cs")

    if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion del plugin." }

    & $csc -nologo -target:exe -platform:x64 -out:"$bin\NwdExtractor.exe" `
        -r:"$($nw.FullName)\Autodesk.Navisworks.Automation.dll" `
        -r:"$($nw.FullName)\Autodesk.Navisworks.Api.dll" `
        -r:System.dll -r:System.Core.dll `
        (Join-Path $raiz "NwdExtractor\Runner.cs")

    if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion del runner." }

    Write-Host "  OK  $bin\NwdExtractor.exe" -ForegroundColor Green
}

# --- Validacion de los XAML ---------------------------------------------------
#
# Un XAML mal formado no falla al compilar: falla al ABRIR la ventana en
# Revit, y ahi solo se ve una traza de IronPython enorme que no dice cual es
# el problema. Cargarlos aqui con el mismo parser de WPF da el error real,
# con numero de linea, antes de tocar Revit.
#
# (Un comentario XML con '--' dentro, por ejemplo, es invalido y tumba la
# ventana entera.)

Write-Host ""
Write-Host "Validando los XAML..." -ForegroundColor Cyan

Add-Type -AssemblyName PresentationFramework
$fallos = 0

Get-ChildItem -Recurse (Join-Path $raiz "Revit\pyRevit\ICuatro.extension") -Filter *.xaml | ForEach-Object {
    $texto = Get-Content $_.FullName -Raw -Encoding UTF8

    # Los manejadores de eventos solo se resuelven con el code-behind de
    # pyRevit, asi que se retiran para poder validar la estructura.
    $limpio = [regex]::Replace($texto,
        '\s+(Click|SelectionChanged|TextChanged|Checked|Unchecked|MouseDoubleClick|Loaded)="[^"]*"', '')

    $nombre = $_.FullName.Replace((Join-Path $raiz "Revit\pyRevit\ICuatro.extension\"), "")

    try {
        $xr = [System.Xml.XmlReader]::Create((New-Object System.IO.StringReader($limpio)))
        [void][Windows.Markup.XamlReader]::Load($xr)
        Write-Host "  OK  $nombre" -ForegroundColor Green
    }
    catch {
        $error_raiz = $_.Exception
        while ($error_raiz.InnerException) { $error_raiz = $error_raiz.InnerException }

        Write-Host "  FALLA  $nombre" -ForegroundColor Red
        Write-Host "         $($error_raiz.Message)" -ForegroundColor Red
        $script:fallos++
    }
}

if ($fallos -gt 0) {
    throw "$fallos archivo(s) XAML no se pueden cargar. Corrigelos antes de abrir Revit."
}

Write-Host ""
Write-Host "Listo. Recarga pyRevit: pestana ICuatro > NWD." -ForegroundColor Cyan
Write-Host ""
Write-Host "Prueba desde aqui, sin Revit:" -ForegroundColor DarkGray
Write-Host "  Revit\pyRevit\ICuatro.extension\bin\IfcExtractor.exe probe <archivo.ifc> informe.txt"
Write-Host "  Revit\pyRevit\ICuatro.extension\bin\IfcExtractor.exe extract <archivo.ifc> zona.nwcache caja=x0,y0,x1,y1"

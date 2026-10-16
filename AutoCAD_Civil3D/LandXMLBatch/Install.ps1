# Builds the plugin and installs it as an autoloader bundle for Civil 3D 2025 (current user).
param([string]$Civil3DDir = "C:\Program Files\Autodesk\AutoCAD 2025")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$bundle = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins\ICuatro.LandXmlBatch.bundle"

dotnet build (Join-Path $root "src\LandXmlBatch.csproj") -c Release -nologo -v q "-p:Civil3DDir=$Civil3DDir"
if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion." }

$out = Join-Path $root "src\bin\Release\net8.0-windows"
New-Item -ItemType Directory -Force (Join-Path $bundle "Contents") | Out-Null
Copy-Item (Join-Path $root "bundle\PackageContents.xml") $bundle -Force
try {
    Copy-Item (Join-Path $out "ICuatro.LandXmlBatch.dll") (Join-Path $bundle "Contents") -Force
} catch {
    throw "No se pudo copiar el DLL (Civil 3D abierto?). Cierre Civil 3D y ejecute de nuevo."
}
Write-Host "Instalado en: $bundle"
Write-Host "Abra Civil 3D 2025. Comandos: ICLANDXML, -ICLANDXML, ICSUPLINEAS, ICMOVERZ, -ICMOVERZ."

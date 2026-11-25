# Empaqueta Corriente como ejecutable de Windows con PyInstaller.
# Uso (PowerShell, desde la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File .\build.ps1            -> dist\Corriente\Corriente.exe (carpeta)
#   powershell -ExecutionPolicy Bypass -File .\build.ps1 -OneFile   -> dist\Corriente.exe (un solo archivo)
param([switch]$OneFile)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-build.txt

& $py tools\make_icon.py

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
& $py -m PyInstaller main.py `
    --name Corriente `
    --windowed `
    $mode `
    --noconfirm --clean `
    --icon assets\corriente.ico `
    --collect-all libtorrent `
    --exclude-module tkinter

if ($LASTEXITCODE -ne 0) { throw "PyInstaller ha fallado" }
Write-Host ""
if ($OneFile) { Write-Host "Listo: dist\Corriente.exe" } else { Write-Host "Listo: dist\Corriente\Corriente.exe" }

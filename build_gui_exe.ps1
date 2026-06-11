$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
    $PythonArgs = @("-3.13")
} else {
    $PythonExe = "python"
    $PythonArgs = @()
}

& $PythonExe @PythonArgs -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] PyInstaller not found. Installing into the active Python environment..."
    & $PythonExe @PythonArgs -m pip install pyinstaller
}

Write-Host "[build] Building MAICA GUI executable..."
& $PythonExe @PythonArgs -m PyInstaller --clean --noconfirm "maica gui\maica_gui.spec"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "[build] Output: dist\maica-gui\maica-gui.exe"

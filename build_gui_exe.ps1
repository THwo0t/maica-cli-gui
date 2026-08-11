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
$StageRoot = Join-Path $Root "build\package_stage"
$StageCli = Join-Path $StageRoot "maica cli"
$StageAssets = Join-Path $StageRoot "maica gui assets\runtime"
if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StageCli -Force | Out-Null
New-Item -ItemType Directory -Path $StageAssets -Force | Out-Null

$excludeNames = @("config.json", "maica_cli.db", "__pycache__", "logs")
$excludeExtensions = @(".db", ".faiss")
Get-ChildItem -LiteralPath (Join-Path $Root "maica cli") -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring((Join-Path $Root "maica cli").Length).TrimStart("\")
    if ($relative -like "logs\*" -or $relative -like "__pycache__\*" -or $relative -like "data\*.faiss" -or $relative -like "data\*_meta.jsonl") {
        return
    }
    if ($excludeNames -contains $_.Name -or $excludeExtensions -contains $_.Extension) {
        return
    }
    $target = Join-Path $StageCli $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
}

Copy-Item -LiteralPath (Join-Path $Root "maica gui assets\runtime\*") -Destination $StageAssets -Recurse -Force

$GuiArgs = @(
    "--clean",
    "--noconfirm",
    "--windowed",
    "--name", "maica-gui",
    "--paths", "maica gui",
    "--paths", "maica cli",
    "--add-data", "$StageCli;maica cli",
    "--add-data", "$StageAssets;maica gui assets\runtime",
    "--exclude-module", "torch",
    "--exclude-module", "transformers",
    "--exclude-module", "sentence_transformers",
    "--exclude-module", "faiss",
    "maica gui\gui_app.py"
)
& $PythonExe @PythonArgs -m PyInstaller @GuiArgs

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "[build] Building embedding service executable..."
$ServiceArgs = @(
    "--clean",
    "--noconfirm",
    "--console",
    "--name", "maica-embedding-service",
    "--paths", "maica cli",
    "--add-data", "$StageCli;maica cli",
    "maica cli\embedding_service.py"
)
& $PythonExe @PythonArgs -m PyInstaller @ServiceArgs

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$GuiDist = Join-Path $Root "dist\maica-gui"
$ServiceExe = Join-Path $Root "dist\maica-embedding-service\maica-embedding-service.exe"
if (Test-Path -LiteralPath $ServiceExe) {
    Copy-Item -LiteralPath $ServiceExe -Destination (Join-Path $GuiDist "maica-embedding-service.exe") -Force
}

& $PythonExe @PythonArgs "maica gui\package_audit.py" $GuiDist --require-runtime
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "[build] Output: dist\maica-gui\maica-gui.exe"
Write-Host "[build] Service: dist\maica-gui\maica-embedding-service.exe"

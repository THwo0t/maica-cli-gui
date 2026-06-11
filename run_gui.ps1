$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.13 "maica gui\gui_app.py" @args
} else {
    & python "maica gui\gui_app.py" @args
}

exit $LASTEXITCODE

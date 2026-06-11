$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.13 "maica gui\smoke_tests.py" @args
} else {
    & python "maica gui\smoke_tests.py" @args
}

exit $LASTEXITCODE

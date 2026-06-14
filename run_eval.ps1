$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.13 "maica cli\eval\run_eval.py" @args
} else {
    & python "maica cli\eval\run_eval.py" @args
}

exit $LASTEXITCODE

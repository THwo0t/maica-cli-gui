$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.13 "maica cli\maica_cli.py" @args
} else {
    & python "maica cli\maica_cli.py" @args
}

exit $LASTEXITCODE

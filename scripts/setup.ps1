param(
    [ValidateSet("tiny", "base", "small")]
    [string]$Model = "base",
    [switch]$Blas
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Python)) {
    python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

$SetupArgs = @("-m", "voicely_alt", "setup", "--model", $Model)
if ($Blas) {
    $SetupArgs += "--blas"
}

& $Python @SetupArgs


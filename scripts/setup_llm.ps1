param(
    [string]$Model = "llama3.2:3b"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $env:USERPROFILE ".redmic_dictate"
$LlmDir = Join-Path $AppDir "llm"
$OllamaDir = Join-Path $LlmDir "ollama"
$ModelDir = Join-Path $LlmDir "models"
$LogDir = Join-Path $AppDir "logs"

New-Item -ItemType Directory -Force -Path $OllamaDir, $ModelDir, $LogDir | Out-Null

function Find-OllamaExe {
    $LocalExe = Join-Path $OllamaDir "ollama.exe"
    if (Test-Path $LocalExe) {
        return $LocalExe
    }

    $Command = Get-Command "ollama.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    return $null
}

$OllamaExe = Find-OllamaExe
if (-not $OllamaExe) {
    Write-Host "Downloading Ollama portable runtime..."
    $Release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/ollama/ollama/releases/latest" `
        -Headers @{ "User-Agent" = "redmic-dictate-setup" }
    $Asset = $Release.assets |
        Where-Object { $_.name -eq "ollama-windows-amd64.zip" } |
        Select-Object -First 1

    if (-not $Asset) {
        throw "Could not find ollama-windows-amd64.zip in the latest Ollama release."
    }

    $ZipPath = Join-Path $LlmDir "ollama-windows-amd64.zip"
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $ZipPath
    tar -xf $ZipPath -C $OllamaDir
    $OllamaExe = Join-Path $OllamaDir "ollama.exe"
}

$env:OLLAMA_HOST = "127.0.0.1:11434"
$env:OLLAMA_MODELS = $ModelDir

try {
    Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    $StdOut = Join-Path $LogDir "ollama.out.log"
    $StdErr = Join-Path $LogDir "ollama.err.log"
    Start-Process `
        -FilePath $OllamaExe `
        -ArgumentList "serve" `
        -WorkingDirectory (Split-Path $OllamaExe) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdOut `
        -RedirectStandardError $StdErr | Out-Null
    Start-Sleep -Seconds 4
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    py -3.11 -m venv (Join-Path $Root ".venv")
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -e $Root
}

& $Python -m voicely_alt setup-llm --model $Model

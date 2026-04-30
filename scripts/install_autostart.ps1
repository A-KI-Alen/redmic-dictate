$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $Root "scripts\start.ps1"
$AppDir = Join-Path $env:USERPROFILE ".redmic_dictate"
$LauncherPath = Join-Path $AppDir "start_redmic_autostart.ps1"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "RedMic Dictate.lnk"
$LegacyShortcutPath = Join-Path $Startup "Voicely Alternative.lnk"

if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}

New-Item -ItemType Directory -Path $AppDir -Force | Out-Null

$EscapedRoot = $Root.Replace("'", "''")
$Launcher = @"
`$ErrorActionPreference = "Continue"

`$Root = '$EscapedRoot'
`$Python = Join-Path `$Root ".venv\Scripts\python.exe"
`$LogDir = Join-Path `$env:USERPROFILE ".redmic_dictate\logs"
`$LogPath = Join-Path `$LogDir "autostart.log"

New-Item -ItemType Directory -Path `$LogDir -Force | Out-Null

function Write-AutostartLog {
    param([string]`$Message)
    `$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "`$Timestamp `$Message" | Add-Content -Path `$LogPath -Encoding UTF8
}

Write-AutostartLog "launcher started; root=`$Root"

for (`$Attempt = 1; `$Attempt -le 60; `$Attempt++) {
    if (Test-Path -LiteralPath `$Python) {
        Write-AutostartLog "starting RedMic Dictate; attempt=`$Attempt"
        Set-Location -LiteralPath `$Root
        & `$Python -m voicely_alt run
        Write-AutostartLog "RedMic Dictate exited with code `$LASTEXITCODE"
        exit `$LASTEXITCODE
    }
    Start-Sleep -Seconds 5
}

Write-AutostartLog "RedMic Dictate was not started because Python was not found: `$Python"
exit 1
"@

Set-Content -Path $LauncherPath -Value $Launcher -Encoding UTF8

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = (Get-Command powershell.exe).Source
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""
$Shortcut.WorkingDirectory = $AppDir
$Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,220"
$Shortcut.Save()

if (Test-Path $LegacyShortcutPath) {
    Remove-Item -LiteralPath $LegacyShortcutPath -Force
}

Write-Host "Autostart installed: $ShortcutPath"
Write-Host "Autostart launcher: $LauncherPath"

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $Root "scripts\start.ps1"
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "Voicely Alternative.lnk"

if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = (Get-Command powershell.exe).Source
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""
$Shortcut.WorkingDirectory = $Root
$Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,220"
$Shortcut.Save()

Write-Host "Autostart installed: $ShortcutPath"


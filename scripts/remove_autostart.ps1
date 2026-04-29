$ErrorActionPreference = "Stop"

$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "RedMic Dictate.lnk"
$LegacyShortcutPath = Join-Path $Startup "Voicely Alternative.lnk"

if (Test-Path $ShortcutPath) {
    Remove-Item -LiteralPath $ShortcutPath -Force
    Write-Host "Autostart removed: $ShortcutPath"
} else {
    Write-Host "Autostart entry not found."
}

if (Test-Path $LegacyShortcutPath) {
    Remove-Item -LiteralPath $LegacyShortcutPath -Force
    Write-Host "Legacy autostart removed: $LegacyShortcutPath"
}

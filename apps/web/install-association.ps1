# Installs per-user .bmo file association (no admin needed, HKCU only).
# Double-click a .bmo file -> native BMO Viewer desktop app (no browser, no Office).
# Run:  powershell -ExecutionPolicy Bypass -File install-association.ps1
$ErrorActionPreference = "Stop"
$AppsDir = Split-Path $MyInvocation.MyCommand.Path -Parent          # ...\bmo\apps\web
$AppsParent = Split-Path $AppsDir -Parent                            # ...\bmo\apps
$RepoRoot = Split-Path $AppsParent -Parent                           # ...\bmo
$Exe = Join-Path $RepoRoot "dist\BMOViewer.exe"
$Viewer = Join-Path $AppsParent "desktop\bmo-view.bat"
if (Test-Path $Exe) { $Opener = $Exe }          # preferred: zero-dependency exe
elseif (Test-Path $Viewer) { $Opener = $Viewer } # fallback: needs Python
else { $Opener = Join-Path $AppsDir "open-bmo.bat" }
if (-not (Test-Path $Opener)) { throw "opener not found: $Opener" }
$Icon = Join-Path $AppsDir "bmo.ico"

New-Item -Path "HKCU:\Software\Classes\.bmo" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Classes\.bmo" -Name "(Default)" -Value "bmo.File"
Set-ItemProperty -Path "HKCU:\Software\Classes\.bmo" -Name "Content Type" -Value "application/x-bmo"
New-Item -Path "HKCU:\Software\Classes\bmo.File" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Classes\bmo.File" -Name "(Default)" -Value "BMO Document"
if (Test-Path $Icon) {
    New-Item -Path "HKCU:\Software\Classes\bmo.File\DefaultIcon" -Force | Out-Null
    Set-ItemProperty -Path "HKCU:\Software\Classes\bmo.File\DefaultIcon" -Name "(Default)" -Value "`"$Icon`",0"
}
New-Item -Path "HKCU:\Software\Classes\bmo.File\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Classes\bmo.File\shell\open\command" -Name "(Default)" -Value "`"$Opener`" `"%1`""
Write-Host "Done. .bmo files now open in BMO Viewer (desktop app)."
Write-Host "If icons look stale, restart Explorer or sign out/in once."

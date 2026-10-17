# Removes the per-user .bmo file association. Run:
# powershell -ExecutionPolicy Bypass -File uninstall-association.ps1
$ErrorActionPreference = "Continue"
Remove-Item -Path "HKCU:\Software\Classes\.bmo" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "HKCU:\Software\Classes\bmo.File" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Removed. .bmo files are unassociated again."

@echo off
rem BMO file opener: double-click a .bmo -> view it in BMO Studio.
setlocal
set "F=%~1"
if "%F%"=="" (
  echo Usage: open-bmo.bat file.bmo
  exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open-bmo.ps1" -File "%F%"

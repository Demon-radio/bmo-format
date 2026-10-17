@echo off
rem BMO Viewer launcher: double-click a .bmo -> native desktop viewer.
setlocal
set "APP=%~dp0viewer.py"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%APP%" "%~1"
) else (
  start "" /min python "%APP%" "%~1"
)

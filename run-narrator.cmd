@echo off
rem Narrator RPG launcher. Usage: run-narrator.cmd [port] [host]   (default 8082, 0.0.0.0)
rem Note: Talemate also uses 8082 - do not run both at once.
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" if defined NARRATOR_PORT set PORT=%NARRATOR_PORT%
if "%PORT%"=="" set PORT=8082
set HOST=%2
if "%HOST%"=="" set HOST=0.0.0.0
echo Narrator RPG on http://%HOST%:%PORT%  (LLM %LLM_URL%)
".venv\Scripts\python.exe" -m app.server --host %HOST% --port %PORT%

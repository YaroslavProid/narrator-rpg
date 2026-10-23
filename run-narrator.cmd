@echo off
rem Narrator RPG launcher. Usage: run-narrator.cmd [port] [host]   (default 8082, 0.0.0.0)
rem Note: Talemate also uses 8082 - do not run both at once.
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" if defined NARRATOR_PORT set PORT=%NARRATOR_PORT%
if "%PORT%"=="" set PORT=8082
set HOST=%2
if "%HOST%"=="" if defined NARRATOR_HOST set HOST=%NARRATOR_HOST%
if "%HOST%"=="" set HOST=0.0.0.0
set LLM_SHOW=%LLM_URL%
if "%LLM_SHOW%"=="" set LLM_SHOW=http://localhost:11435
if not exist ".venv\Scripts\python.exe" (
  echo [!] .venv not found. Run first:
  echo     py -3.12 -m venv .venv
  echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo Narrator RPG on http://localhost:%PORT%  (bind %HOST%, LLM %LLM_SHOW%)
".venv\Scripts\python.exe" -m app.server --host %HOST% --port %PORT%
if errorlevel 1 pause

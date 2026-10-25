@echo off
rem One-click start: llama-server (if it is not running yet) + Narrator RPG + browser.
rem Usage: start-narrator.cmd [port] [host]   (default 8082, 0.0.0.0 - same as run-narrator.cmd)
rem Env:   LLAMA_START  - script that starts llama-server (default C:\games\llama.cpp\start-gemma26.cmd)
rem        LLM_URL      - llama-server address (default http://localhost:11435)
rem        NARRATOR_NO_BROWSER=1 - do not open the browser
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" if defined NARRATOR_PORT set PORT=%NARRATOR_PORT%
if "%PORT%"=="" set PORT=8082
set HOST=%2
if not defined LLAMA_START set LLAMA_START=C:\games\llama.cpp\start-gemma26.cmd
set LLM=%LLM_URL%
if "%LLM%"=="" set LLM=http://localhost:11435
set URL=http://localhost:%PORT%

rem --- already running? just open it
curl -s -m 2 %URL%/api/status >nul 2>&1
if not errorlevel 1 (
  echo Narrator RPG is already running on %URL%
  if not "%NARRATOR_NO_BROWSER%"=="1" start "" %URL%
  exit /b 0
)

rem --- llama-server
curl -s -m 2 %LLM%/health >nul 2>&1
if errorlevel 1 (
  if exist "%LLAMA_START%" (
    echo Starting llama-server: %LLAMA_START%
    start "llama-server" /min cmd /c "%LLAMA_START%"
  ) else (
    echo [!] llama-server is not running and "%LLAMA_START%" was not found.
    echo     Start it yourself or set LLAMA_START to its launch script.
  )
)
echo Waiting for the model at %LLM% ...
set /a TRIES=0
:wait_llm
curl -s -m 2 %LLM%/health | findstr /r /c:"status.:.ok" >nul
if not errorlevel 1 goto llm_ready
set /a TRIES+=1
if %TRIES% geq 100 (
  echo [!] The model did not become ready in 5 minutes - starting Narrator RPG anyway.
  goto llm_ready
)
timeout /t 3 /nobreak >nul
goto wait_llm
:llm_ready
echo Model is ready.

rem --- open the browser a few seconds after the server starts, then run it in this window
if not "%NARRATOR_NO_BROWSER%"=="1" start "" /min cmd /c "timeout /t 5 /nobreak >nul & start %URL%"
call "%~dp0run-narrator.cmd" %PORT% %HOST%

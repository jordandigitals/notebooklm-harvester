@echo off
rem NotebookLM Harvester launcher for Windows. Double-click to start.
rem Creates or reuses .venv next to this file, installs pinned requirements when they
rem change, checks ffmpeg, then starts the local page in your browser.
setlocal
cd /d "%~dp0"

set "PY="
for %%V in (3.14 3.13 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python 3.11 to 3.14 is needed. Install it from python.org or with: winget install Python.Python.3.13
  pause
  exit /b 1
)

where ffmpeg >nul 2>&1 || goto noffmpeg
where ffprobe >nul 2>&1 || goto noffmpeg

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up, about a minute...
  %PY% -m venv .venv || goto fail
)

for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash requirements.txt -Algorithm SHA256).Hash"`) do set "REQ_HASH=%%H"
set "OLD_HASH="
if exist ".venv\.req-hash" set /p OLD_HASH=<".venv\.req-hash"
if not "%REQ_HASH%"=="%OLD_HASH%" (
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip || goto fail
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto fail
  > ".venv\.req-hash" echo %REQ_HASH%
)

".venv\Scripts\python.exe" -m nlh serve
exit /b %errorlevel%

:noffmpeg
echo ffmpeg and ffprobe are needed. Install them with: winget install Gyan.FFmpeg
echo Then close this window and double-click run-windows.bat again.
pause
exit /b 1

:fail
echo Setup failed. See the messages above.
pause
exit /b 1

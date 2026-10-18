@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3 -m venv .venv || python -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)
if not exist "frontend\node_modules" (
  echo Installing frontend packages...
  call npm --prefix frontend install || goto :fail
)

start "Blendex backend" /d "%~dp0." cmd /k .venv\Scripts\python.exe -m backend.app
start "Blendex frontend" /d "%~dp0." cmd /k npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173 --strictPort --open
exit /b 0

:fail
echo Setup failed, see the messages above.
pause
exit /b 1

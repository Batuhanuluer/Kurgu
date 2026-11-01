@echo off
REM Kurgu - Windows baslatici. Ilk calistirmada sanal ortam kurar.
cd /d "%~dp0"
if not exist .venv (
  echo Ilk kurulum yapiliyor, birkac dakika surebilir...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.10+ kurun.
    pause & exit /b 1
  )
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m kurgu %*

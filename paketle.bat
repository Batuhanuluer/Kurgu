@echo off
REM Kurgu - Windows paketleyici: dist\ klasorune Kurgu.exe (ve Inno Setup kuruluysa kurulum programi) uretir.
cd /d "%~dp0"
if not exist .venv (
  echo Sanal ortam kuruluyor...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.10+ kurun.
    pause & exit /b 1
  )
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m pip install pyinstaller pillow
.venv\Scripts\python packaging\build.py
if errorlevel 1 ( echo Paketleme basarisiz oldu. & pause & exit /b 1 )
echo.
echo Hazir! dist\Kurgu\Kurgu.exe dosyasina cift tiklayarak acabilirsiniz.
explorer dist
pause

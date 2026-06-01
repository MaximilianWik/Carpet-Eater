@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo creating venv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet pyinstaller>=6.0

if not exist vendor\ffmpeg.exe (
    echo.
    echo ERROR: vendor\ffmpeg.exe is missing.
    echo Download from https://www.gyan.dev/ffmpeg/builds/ ("essentials" build)
    echo and place ffmpeg.exe in vendor\.
    exit /b 1
)

echo.
echo == generating icon ==
python make_icon.py || exit /b 1

echo.
echo == cleaning previous build ==
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo.
echo == running PyInstaller ==
pyinstaller --noconfirm build.spec || exit /b 1

echo.
echo == done ==
dir dist\CarpetEater.exe
echo.
echo Output: dist\CarpetEater.exe

@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Exness Auto-Trade - One Click Start

echo.
echo =====================================================================
echo   EXNESS AUTO-TRADE  ^|  1 lenh: cai thieu + start web + MT5
echo =====================================================================
echo.

set "PYEXE="
where python >nul 2>&1 && for /f "delims=" %%I in ('where python') do (
    set "PYEXE=%%I"
    goto :have_python
)

where py >nul 2>&1
if not errorlevel 1 (
    echo [0/6] Dung Python Launcher: py -3
    py -3 "%~dp0start_windows.py" %*
    if errorlevel 1 goto :fail
    goto :eof
)

echo [0/6] Chua co Python. Dang cai Python 3.12 ...
where winget >nul 2>&1
if not errorlevel 1 (
    winget install -e --id Python.3.12 --accept-package-agreements --accept-source-agreements --disable-interactivity
) else (
    echo       winget khong co. Tai installer Python chinh thuc ...
    mkdir "%TEMP%\exness-setup" >nul 2>&1
    curl.exe -L --retry 3 -o "%TEMP%\exness-setup\python-3.12.10-amd64.exe" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    if not exist "%TEMP%\exness-setup\python-3.12.10-amd64.exe" goto :no_python
    "%TEMP%\exness-setup\python-3.12.10-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
)

set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
where python >nul 2>&1 && for /f "delims=" %%I in ('where python') do (
    set "PYEXE=%%I"
    goto :have_python
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :have_python
)

:no_python
echo [LOI] Khong cai duoc Python. Cai tay tai https://www.python.org/
echo       Nho TICH "Add python.exe to PATH".
pause
exit /b 1

:have_python
echo [0/6] Python: %PYEXE%
"%PYEXE%" "%~dp0start_windows.py" %*
if errorlevel 1 goto :fail
goto :eof

:fail
echo.
echo [LOI] Start that bai. Xem log phia tren.
pause
exit /b 1

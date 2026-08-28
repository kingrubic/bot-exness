@echo off
title Exness Auto-Trade AI Platform - Windows Native MT5
echo =====================================================================
echo   KHOI DONG HE THONG EXNESS AUTO-TRADE AI TRADING BOT (WINDOWS NATIVE)
echo =====================================================================
echo.

:: 1. Kiem tra Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOI] Khong tim thay Python tren he thong.
    echo Vui long cai dat Python 3.10+ tu https://www.python.org/
    echo LUU Y: Nho tich chon vao muc "Add Python to PATH" khi cai dat.
    pause
    exit /b
)

:: 2. Cai dat dependencies (MetaTrader5, Django, pandas, numpy...)
echo [1/3] Dang kiem tra thu vien (pip install -r requirements.txt)...
pip install -r requirements.txt

:: 3. Khoi dong Web Dashboard, WebSocket Realtime va Bot Giao Dich Exness
echo.
echo [2/3] Dang khoi dong he thong Auto-Trade ket noi truc tiep MT5 Exness...
echo [3/3] San sang truy cap Dashboard tai: http://localhost:8000/admin-panel/
echo.
start http://localhost:8000/admin-panel/
python run_bot.py 8000

pause

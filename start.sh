#!/usr/bin/env bash
# Ubuntu / Linux one-click start — tương đương Windows start.bat
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export DISPLAY="${DISPLAY:-:0}"
export WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}"

echo
echo "====================================================================="
echo "  EXNESS AUTO-TRADE  |  1 lệnh: cài thiếu + start web + MT5 + bridge"
echo "====================================================================="
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "[0/6] Chưa có Python. Đang cài python3 python3-venv python3-pip ..."
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip
fi

echo "[0/6] Python: $(command -v python3) ($(python3 --version 2>&1))"
exec python3 start_linux.py "$@"

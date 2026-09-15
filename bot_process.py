"""Tắt process bot/web cũ trước khi start bản mới (tránh chạy chồng, chiếm cổng)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time


def stop_existing_bot(port: str | int = '8888', log=print) -> int:
    """Chỉ tắt python run_bot.py / listener cổng khác — không đụng process hiện tại và cha."""
    keep = _ancestor_pids(os.getpid())
    targets = set()

    for row in _python_bot_processes():
        pid = int(row.get('ProcessId') or 0)
        if pid and pid not in keep:
            targets.add(pid)

    for pid in _pids_listening(port):
        if pid not in keep:
            targets.add(pid)

    targets -= keep
    if not targets:
        log('     Không có bot cũ đang chạy.')
        return 0

    log(f'     Đang tắt {len(targets)} process bot cũ: {", ".join(str(p) for p in sorted(targets))}')
    for pid in sorted(targets):
        _kill_pid(pid)

    deadline = time.time() + 8
    while time.time() < deadline:
        still = {p for p in targets if _pid_alive(p)}
        still |= {p for p in _pids_listening(port) if p not in keep}
        still -= keep
        if not still:
            break
        time.sleep(0.25)
        for pid in list(still):
            _kill_pid(pid)
    time.sleep(0.3)
    return len(targets)


def _ancestor_pids(pid: int) -> set[int]:
    seen: set[int] = set()
    cur = int(pid or 0)
    for _ in range(12):
        if cur <= 4 or cur in seen:
            break
        seen.add(cur)
        nxt = _parent_pid(cur)
        if nxt <= 4 or nxt == cur:
            break
        cur = nxt
    return seen


def _parent_pid(pid: int) -> int:
    if sys.platform != 'win32':
        try:
            return os.getppid() if pid == os.getpid() else 0
        except Exception:
            return 0
    try:
        out = subprocess.check_output(
            [
                'powershell', '-NoProfile', '-Command',
                f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").ParentProcessId',
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
        return int((out or '0').strip() or 0)
    except Exception:
        return 0


def _python_bot_processes() -> list[dict]:
    """Chỉ python.exe đang chạy run_bot.py — không match PowerShell đang query."""
    if sys.platform != 'win32':
        return []
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.Name -match '^python' -and $_.CommandLine -and "
        "($_.CommandLine -match 'run_bot\\.py' -or $_.CommandLine -match 'start_windows\\.py') "
        "} | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
            capture_output=True, text=True, timeout=20,
        )
        raw = (r.stdout or '').strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        return list(data or [])
    except Exception:
        return []


def _pids_listening(port: str | int) -> set[int]:
    port = str(port)
    try:
        r = subprocess.run(
            ['netstat', '-ano', '-p', 'tcp'],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return set()
    pids: set[int] = set()
    pat = re.compile(rf'TCP\s+\S+:{re.escape(port)}\s+\S+\s+LISTENING\s+(\d+)', re.I)
    for line in (r.stdout or '').splitlines():
        m = pat.search(line)
        if m:
            n = int(m.group(1))
            if n > 0:
                pids.add(n)
    return pids


def _kill_pid(pid: int) -> None:
    """Kill đúng PID, không /T — tránh giết cả cây start_windows → run_bot hiện tại."""
    if pid <= 0 or pid == os.getpid():
        return
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    subprocess.run(
        ['taskkill', '/F', '/PID', str(pid)],
        capture_output=True, timeout=15, creationflags=creation,
    )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        r = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10,
        )
        out = (r.stdout or '').strip()
        return str(pid) in out and 'No tasks' not in out and 'INFO:' not in out
    except Exception:
        return False

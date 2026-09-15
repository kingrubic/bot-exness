#!/usr/bin/env python
"""One-click Windows bootstrap: install missing pieces, then start web + MT5."""
import os
import sys
import time
import shutil
import webbrowser
import subprocess
import threading
import importlib.util
from pathlib import Path

from bot_process import stop_existing_bot

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / 'venv' / 'Scripts' / 'python.exe'
os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')


def log(msg):
    print(msg, flush=True)


def ensure_venv():
    if VENV_PY.exists():
        return
    log('[1/6] Chưa có venv → đang tạo python -m venv venv ...')
    subprocess.check_call([sys.executable, '-m', 'venv', str(ROOT / 'venv')])
    log('     ✅ Đã tạo venv')


def reexec_in_venv():
    if Path(sys.executable).resolve() == VENV_PY.resolve():
        return
    log('[1/6] Chuyển sang Python trong venv ...')
    # Windows os.execv không thay process đúng cách → start.bat bị trả prompt sớm.
    raise SystemExit(subprocess.call(
        [str(VENV_PY), str(ROOT / 'start_windows.py'), *sys.argv[1:]],
        cwd=str(ROOT),
    ))


def ensure_pip_packages():
    req = ROOT / 'requirements.txt'
    log('[2/6] Kiểm tra / cài thư viện (pip install -r requirements.txt) ...')
    subprocess.check_call([str(VENV_PY), '-m', 'pip', 'install', '-r', str(req)])


def ensure_env():
    env_file = ROOT / '.env'
    example = ROOT / '.env.example'
    if not env_file.exists():
        if not example.exists():
            raise SystemExit('Thiếu .env.example — không tạo được file cấu hình.')
        shutil.copy2(example, env_file)
        log('[3/6] ✅ Đã tạo .env từ .env.example')
    else:
        log('[3/6] ✅ Đã có file .env')


def load_mt5_launcher():
    spec = importlib.util.spec_from_file_location(
        'mt5_launcher', ROOT / 'apps' / 'trading' / 'mt5_launcher.py'
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MT5Launcher


def ensure_mt5():
    log('[4/6] Kiểm tra / cài / mở Exness MetaTrader 5 ...')
    launcher = load_mt5_launcher()
    launcher.ensure_terminal_running()


def ensure_admin(username='admin', password='123123123'):
    log(f'[5/6] Tạo / cập nhật tài khoản admin ({username}) ...')
    subprocess.check_call([str(VENV_PY), str(ROOT / 'create_admin.py'), username, password])


def open_browser_later(url, delay=4):
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def start_app(port):
    log('[6/6] Tắt bot cũ lần nữa (nếu còn) rồi bật Waitress ...')
    stop_existing_bot(port, log=log)
    log(f'     Khởi động Web + Bot Worker tại http://localhost:{port}/')
    log('     Login: http://localhost:%s/login/   (admin / 123123123)' % port)
    log('     Ctrl+C để dừng.\n')
    open_browser_later(f'http://localhost:{port}/login/')
    raise SystemExit(subprocess.call(
        [str(VENV_PY), '-u', str(ROOT / 'run_bot.py'), str(port)],
        cwd=str(ROOT),
    ))


def main():
    os.chdir(ROOT)
    log('=' * 70)
    log('  EXNESS AUTO-TRADE — Windows one-click start')
    log('  1 lệnh: cài cái còn thiếu, rồi start web + MT5')
    log('=' * 70)

    if sys.version_info < (3, 10):
        raise SystemExit('Cần Python 3.10+. Hiện tại: %s' % sys.version)

    port = '8888'
    if len(sys.argv) > 1 and str(sys.argv[1]).replace(':', '').isdigit():
        port = sys.argv[1].split(':')[-1]
    else:
        env_port = os.getenv('DJANGO_PORT', '').strip()
        if env_port.isdigit():
            port = env_port

    ensure_venv()
    reexec_in_venv()
    log('[0/6] Tắt bot đang chạy (nếu có) rồi start bản mới ...')
    stop_existing_bot(port, log=log)
    ensure_pip_packages()
    ensure_env()
    ensure_mt5()
    ensure_admin()
    start_app(port)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log('\nĐã dừng.')
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        log(f'\n[LỖI] Lệnh thất bại (exit {e.returncode}): {e.cmd}')
        sys.exit(e.returncode)

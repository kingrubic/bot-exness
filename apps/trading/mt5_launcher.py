import os
import sys
import time
import shutil
import threading
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALLER_URL = 'https://download.mql5.com/cdn/web/exness.technologies.ltd/mt5/exness5setup.exe'
INSTALLER_PATH = PROJECT_ROOT / 'tools' / 'exness5setup.exe'


class MT5Launcher:
    """
    Tự động phát hiện và khởi động phần mềm Exness MT5 Terminal khi chạy dự án trên Windows.
    """

    COMMON_PATHS = [
        # 1. Project local portable folder
        str(PROJECT_ROOT / 'tools' / 'mt5' / 'terminal64.exe'),
        str(PROJECT_ROOT / 'mt5' / 'terminal64.exe'),

        # 2. Windows standard installation paths
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
        r"C:\Program Files\Exness MetaTrader 5\terminal64.exe",
        r"C:\Program Files\Exness MT5\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5 EXNESS\terminal64.exe",
        r"C:\Program Files (x86)\Exness MetaTrader 5\terminal64.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\MetaTrader 5\terminal64.exe"),
        os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Community\terminal64.exe"),

        # 3. Linux Wine default paths
        os.path.expanduser(r"~/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"),
        os.path.expanduser(r"~/.wine/drive_c/Program Files/Exness MetaTrader 5/terminal64.exe"),
    ]

    @classmethod
    def get_mt5_path(cls) -> str:
        """Tìm đường dẫn file thực thi terminal64.exe."""
        custom_path = os.environ.get('MT5_TERMINAL_PATH', '').strip()
        if custom_path and os.path.exists(custom_path):
            return custom_path

        for path in cls.COMMON_PATHS:
            if path and os.path.exists(path):
                return path

        for root in (
            os.environ.get('ProgramFiles', r'C:\Program Files'),
            os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
        ):
            if not root or not os.path.isdir(root):
                continue
            try:
                for name in os.listdir(root):
                    candidate = os.path.join(root, name, 'terminal64.exe')
                    if os.path.isfile(candidate):
                        return candidate
            except OSError:
                continue
        return None

    @classmethod
    def is_mt5_process_running(cls) -> bool:
        """Kiểm tra tiến trình terminal64.exe có đang chạy không."""
        try:
            if sys.platform == 'win32':
                output = subprocess.check_output(['tasklist'], encoding='utf-8', errors='ignore')
                return 'terminal64.exe' in output.lower() or 'terminal.exe' in output.lower()
            output = subprocess.check_output(['pgrep', '-f', 'terminal64.exe'], encoding='utf-8', errors='ignore')
            return bool(output.strip())
        except Exception:
            return False

    @classmethod
    def copy_ea_files(cls, terminal_path: str) -> None:
        """Copy EA MQL5 từ source vào thư mục Experts (Program Files hoặc AppData)."""
        src_dir = PROJECT_ROOT / 'mql5'
        if not src_dir.exists():
            return

        dests = [Path(terminal_path).parent / 'MQL5' / 'Experts']
        roaming = Path(os.path.expandvars(r'%APPDATA%\MetaQuotes\Terminal'))
        if roaming.is_dir():
            for child in roaming.iterdir():
                experts = child / 'MQL5' / 'Experts'
                if experts.parent.is_dir():
                    dests.append(experts)

        copied = False
        for dest_dir in dests:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for src in src_dir.glob('*.mq5'):
                    shutil.copy2(src, dest_dir / src.name)
                copied = True
                logger.info("Copied MQL5 EA files to %s", dest_dir)
            except OSError as e:
                logger.debug("Skip EA dest %s: %s", dest_dir, e)
        if not copied:
            logger.warning("Could not copy EA files into any MT5 Experts folder")

    @classmethod
    def persist_terminal_path(cls, terminal_path: str) -> None:
        """Ghi MT5_TERMINAL_PATH vào .env để lần sau khỏi dò đường dẫn."""
        env_file = PROJECT_ROOT / '.env'
        if not env_file.exists():
            return
        lines = env_file.read_text(encoding='utf-8').splitlines()
        out = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('MT5_TERMINAL_PATH=') or stripped.startswith('# MT5_TERMINAL_PATH=') or stripped.startswith('#MT5_TERMINAL_PATH='):
                out.append(f'MT5_TERMINAL_PATH={terminal_path}')
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f'MT5_TERMINAL_PATH={terminal_path}')
        env_file.write_text('\n'.join(out) + '\n', encoding='utf-8')

    @classmethod
    def download_installer(cls) -> bool:
        """Tải bộ cài Exness MT5 chính thức nếu chưa có."""
        try:
            INSTALLER_PATH.parent.mkdir(parents=True, exist_ok=True)
            if INSTALLER_PATH.exists() and INSTALLER_PATH.stat().st_size > 1_000_000:
                print(f"📦 Đã có bộ cài MT5: {INSTALLER_PATH}")
                return True
            print(f"⬇️ Đang tải Exness MetaTrader 5 từ máy chủ chính thức...")
            import urllib.request

            def _progress(block, block_size, total):
                if total <= 0:
                    return
                done = min(block * block_size, total)
                pct = done * 100 // total
                print(f"\r   {pct}% ({done // 1024} KB / {total // 1024} KB)", end='', flush=True)

            urllib.request.urlretrieve(INSTALLER_URL, INSTALLER_PATH, reporthook=_progress)
            print()
            return INSTALLER_PATH.exists() and INSTALLER_PATH.stat().st_size > 1_000_000
        except Exception as e:
            print(f"⚠️ Không tải được bộ cài MT5: {e}")
            return False

    @classmethod
    def ensure_installed(cls) -> bool:
        """Nếu chưa có terminal64.exe thì tải và cài Exness MT5 (Windows)."""
        path = cls.get_mt5_path()
        if path:
            cls.persist_terminal_path(path)
            cls.copy_ea_files(path)
            print(f"✅ Đã cài Exness MetaTrader 5: {path}")
            return True

        if sys.platform != 'win32':
            print("⚠️ Auto-install MT5 chỉ hỗ trợ Windows. Linux dùng Wine + bridge.")
            return False

        if not cls.download_installer():
            print("   Tải thủ công: https://www.exness.com/metatrader-5/")
            return False

        print("🛠️ Đang cài Exness MetaTrader 5 (cửa sổ UAC có thể hiện — bấm Yes)...")
        try:
            subprocess.run([str(INSTALLER_PATH), '/auto'], check=False)
        except Exception as e:
            print(f"⚠️ Lỗi chạy bộ cài MT5: {e}")
            return False

        for i in range(60):
            time.sleep(2)
            path = cls.get_mt5_path()
            if path:
                cls.persist_terminal_path(path)
                cls.copy_ea_files(path)
                print(f"✅ Cài MT5 xong: {path}")
                return True
            if i in (5, 15, 30):
                print(f"   Đang chờ cài đặt hoàn tất... ({i * 2}s)")

        print("⚠️ Cài MT5 xong nhưng chưa thấy terminal64.exe. Mở lại bộ cài: tools\\exness5setup.exe")
        return False

    @classmethod
    def ensure_terminal_running(cls) -> bool:
        """Cài MT5 nếu thiếu, rồi mở terminal nếu chưa chạy."""
        cls.ensure_installed()

        if cls.is_mt5_process_running():
            print("✅ Exness MetaTrader 5 đang chạy.")
            return True

        if sys.platform != 'win32' and not os.environ.get('DISPLAY'):
            print("⚠️ Máy Linux/SSH không có DISPLAY — bỏ qua mở GUI MT5.")
            return False

        path = cls.get_mt5_path()
        if not path:
            print("⚠️ Chưa tìm thấy Exness MetaTrader 5 (terminal64.exe).")
            print("   Tải tại: https://www.exness.com/metatrader-5/")
            print("   Hoặc chạy: tools\\exness5setup.exe")
            return False

        cls.copy_ea_files(path)

        try:
            print(f"🚀 Đang mở MetaTrader 5: {path}")
            kwargs = {'cwd': os.path.dirname(path)}
            if sys.platform == 'win32':
                kwargs['creationflags'] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            subprocess.Popen([path], **kwargs)
            for _ in range(30):
                time.sleep(0.5)
                if cls.is_mt5_process_running():
                    print("✅ Đã khởi động Exness MetaTrader 5.")
                    return True
            print("⚠️ Đã gọi MT5 nhưng chưa thấy tiến trình terminal64.exe.")
            return False
        except Exception as e:
            print(f"⚠️ Không mở được MT5: {e}")
            return False

    _status_cache = {'ts': 0.0, 'data': None}

    SETUP_STEPS = [
        'Mở phần mềm Exness MetaTrader 5 (terminal64.exe).',
        'Tắt hộp "Open an account" nếu hiện ra (bấm Cancel).',
        'File → Login to Trade Account. Login = số tài khoản MT5, Password = mật khẩu TRADING (không phải mật khẩu web Exness), Server = Exness-MT5Real hoặc Exness-MT5Trial.',
        'Bật nút Algo Trading (phải màu xanh) trên thanh công cụ MT5.',
        'Tools → Options → Expert Advisors: tích "Allow algorithmic trading".',
        'Vào Admin → Ví: điền đúng login / password / server đang mở trong MT5, rồi bấm Kiểm Tra Kết Nối Sàn.',
    ]

    @staticmethod
    def status_code_from_flags(installed: bool, running: bool, logged_in: bool, algo_trading: bool) -> str:
        if not installed:
            return 'not_installed'
        if not running:
            return 'not_running'
        if not logged_in:
            return 'not_logged_in'
        if not algo_trading:
            return 'algo_off'
        return 'ready'

    @classmethod
    def get_runtime_status(cls, probe_api: bool = True, timeout_ms: int = 2500, use_cache: bool = True) -> dict:
        """Trạng thái MT5: đã cài / đang chạy / đã login — bot chỉ giao dịch khi ready=True."""
        now = time.time()
        if use_cache and cls._status_cache['data'] and (now - cls._status_cache['ts']) < 4:
            return cls._status_cache['data']

        installed_path = cls.get_mt5_path()
        running = cls.is_mt5_process_running()
        api_ok = False
        logged_in = False
        algo_trading = False
        account = None
        error = None

        if probe_api and running:
            try:
                from apps.trading.mt5_session import MT5NativeSession
                snap = MT5NativeSession.terminal_snapshot()
                api_ok = bool(snap.get('ready'))
                logged_in = bool(snap.get('logged_in'))
                algo_trading = bool(snap.get('trade_allowed'))
                account = snap.get('account')
                error = snap.get('error')
            except ImportError:
                error = 'Chưa cài gói Python MetaTrader5 (pip install MetaTrader5).'
            except Exception as e:
                error = str(e)

        code = cls.status_code_from_flags(bool(installed_path), running, logged_in, algo_trading)
        if code == 'not_installed':
            title = 'Chưa cài Exness MetaTrader 5 — bot chưa thể giao dịch'
            steps = [
                'Tải và cài Exness MT5: https://www.exness.com/metatrader-5/ (hoặc chạy start.bat để tự cài).',
            ] + list(cls.SETUP_STEPS)
        elif code == 'not_running':
            title = 'Chưa bật MetaTrader 5 — bot chưa thể lấy giá / khớp lệnh'
            steps = list(cls.SETUP_STEPS)
        elif code == 'not_logged_in':
            title = 'MT5 đã mở nhưng chưa đăng nhập tài khoản Exness'
            steps = list(cls.SETUP_STEPS[1:])
        elif code == 'algo_off':
            title = 'Cần bật Algo Trading trên MT5 thì ví/bot mới hoạt động'
            steps = [
                'Trên thanh công cụ MetaTrader 5, bấm nút Algo Trading cho đến khi nó chuyển sang màu xanh.',
                'Tools → Options → Expert Advisors: tích "Allow algorithmic trading".',
                'Sau khi bật, mở lệnh / đóng lệnh / bot mới gửi được lên sàn.',
            ]
        else:
            title = 'MT5 sẵn sàng — bot có thể giao dịch'
            steps = []

        data = {
            'ok': code == 'ready',
            'code': code,
            'title': title,
            'installed': bool(installed_path),
            'path': installed_path,
            'running': running,
            'api_ok': api_ok,
            'logged_in': logged_in,
            'algo_trading': algo_trading,
            'account': account,
            'error': error,
            'steps': steps,
        }
        cls._status_cache = {'ts': now, 'data': data}
        return data

    @classmethod
    def print_setup_guide(cls, status: dict = None) -> None:
        """In hướng dẫn bắt buộc ra terminal khi MT5 chưa sẵn sàng."""
        status = status or cls.get_runtime_status()
        if status.get('ok'):
            acc = status.get('account') or {}
            print()
            print('=' * 72)
            print(f"  ✅ MT5 SẴN SÀNG — tài khoản #{acc.get('login')} ({acc.get('server')})")
            print('     Bot có thể lấy giá live và khớp lệnh.')
            print('=' * 72)
            print()
            return

        print()
        print('!' * 72)
        print(f"  ⚠️  {status.get('title')}")
        if status.get('code') == 'algo_off':
            print('  Turn on the Algo Trading button (it must be green) in MetaTrader 5.')
        else:
            print('  The trading bot cannot work until Exness MT5 is open and logged in.')
        print('!' * 72)
        print()
        print('  Làm lần lượt trong cửa sổ MetaTrader 5:')
        for i, step in enumerate(status.get('steps') or cls.SETUP_STEPS, 1):
            print(f'  {i}. {step}')
        print()
        print('  Web dashboard vẫn mở được, nhưng giá live / mở lệnh / đóng lệnh')
        print('  sẽ KHÔNG hoạt động cho đến khi hoàn tất các bước trên.')
        if status.get('path'):
            print(f'  Đường dẫn MT5: {status["path"]}')
        if status.get('error'):
            print(f'  Chi tiết kỹ thuật: {status["error"]}')
        print('!' * 72)
        print()

    @classmethod
    def notify_windows(cls, status: dict = None) -> None:
        """Popup Windows (không chặn start web) khi MT5 chưa bật / chưa login / chưa Algo."""
        if sys.platform != 'win32':
            return
        status = status or cls.get_runtime_status(use_cache=True)
        if status.get('ok'):
            return

        if status.get('code') == 'algo_off':
            caption = 'Exness Bot — Cần bật Algo Trading'
            lines = [
                'MetaTrader 5 đang mở nhưng nút Algo Trading đang TẮT.',
                '',
                'Ví/bot không gửi hoặc đóng lệnh được cho đến khi bạn bật Algo.',
                '',
                '1. Trên thanh công cụ MT5, bấm Algo Trading cho đến khi nút màu xanh.',
                '2. Tools → Options → Expert Advisors: tích "Allow algorithmic trading".',
            ]
        else:
            caption = 'Exness Bot — Cần MetaTrader 5'
            lines = [status.get('title', 'Cần bật MetaTrader 5'), '', 'Để bot hoạt động, làm lần lượt:']
            for i, step in enumerate(status.get('steps') or cls.SETUP_STEPS, 1):
                lines.append(f'{i}. {step}')
        text = '\n'.join(lines)

        def _show():
            try:
                import ctypes
                MB_ICONWARNING = 0x30
                MB_TOPMOST = 0x40000
                ctypes.windll.user32.MessageBoxW(
                    0,
                    text,
                    caption,
                    MB_ICONWARNING | MB_TOPMOST,
                )
            except Exception:
                pass

        threading.Thread(target=_show, daemon=True).start()

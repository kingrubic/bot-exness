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
PYTHON_WIN_URL = 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe'
PYTHON_WIN_PATH = PROJECT_ROOT / 'tools' / 'python-3.10.11-amd64.exe'
WEBVIEW_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
WEBVIEW_PATH = PROJECT_ROOT / 'tools' / 'MicrosoftEdgeWebview2Setup.exe'


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

        # 3. Linux Wine — prefix dự án (~/.mt5) rồi ~/.wine
        os.path.expanduser(r"~/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe"),
        os.path.expanduser(r"~/.mt5/drive_c/Program Files/Exness MetaTrader 5/terminal64.exe"),
        os.path.expanduser(r"~/.mt5/drive_c/Program Files (x86)/MetaTrader 5/terminal64.exe"),
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
        if sys.platform != 'win32':
            found = cls._scan_wine_terminal()
            if found:
                return found
        return None

    @classmethod
    def _scan_wine_terminal(cls) -> str:
        prefixes = []
        env_p = os.environ.get('WINEPREFIX', '').strip()
        if env_p:
            prefixes.append(env_p)
        prefixes.extend([os.path.expanduser('~/.mt5'), os.path.expanduser('~/.wine')])
        seen = set()
        for prefix in prefixes:
            if not prefix or prefix in seen or not os.path.isdir(prefix):
                continue
            seen.add(prefix)
            for rel in ('drive_c/Program Files', 'drive_c/Program Files (x86)'):
                root = os.path.join(prefix, rel)
                if not os.path.isdir(root):
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
        if sys.platform != 'win32':
            users = Path(cls.wine_prefix()) / 'drive_c' / 'users'
            if users.is_dir():
                for child in users.glob('*/AppData/Roaming/MetaQuotes/Terminal'):
                    if not child.is_dir():
                        continue
                    for term in child.iterdir():
                        experts = term / 'MQL5' / 'Experts'
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
    def _download_file(cls, url: str, dest: Path, min_size: int, label: str) -> bool:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size >= min_size:
                print(f"📦 Đã có {label}: {dest}")
                return True
            print(f"⬇️ Đang tải {label} ...")
            import urllib.request

            def _progress(block, block_size, total):
                if total <= 0:
                    return
                done = min(block * block_size, total)
                pct = done * 100 // total
                print(f"\r   {pct}% ({done // 1024} KB / {max(total, 1) // 1024} KB)", end='', flush=True)

            urllib.request.urlretrieve(url, dest, reporthook=_progress)
            print()
            return dest.exists() and dest.stat().st_size >= min_size
        except Exception as e:
            print(f"⚠️ Không tải được {label}: {e}")
            return False

    @classmethod
    def download_installer(cls) -> bool:
        """Tải bộ cài Exness MT5 chính thức nếu chưa có."""
        return cls._download_file(INSTALLER_URL, INSTALLER_PATH, 1_000_000, 'Exness MetaTrader 5')

    @classmethod
    def _run_root(cls, args: list) -> bool:
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            return subprocess.run(args, env=env).returncode == 0
        for prefix in (['sudo', '-n'], ['sudo']):
            print(f"     $ {' '.join(prefix + args)}")
            r = subprocess.run(prefix + args, env=env)
            if r.returncode == 0:
                return True
            if prefix == ['sudo', '-n']:
                print('     Cần mật khẩu sudo để cài Wine ...')
        return False

    @classmethod
    def ensure_wine_binary(cls) -> bool:
        if shutil.which('wine'):
            return True
        print('📦 Chưa có Wine — đang cài qua apt (cần sudo) ...')
        cls._run_root(['apt-get', 'update', '-y'])
        if not cls._run_root(['apt-get', 'install', '-y', 'wine', 'wine64']):
            cls._run_root(['apt-get', 'install', '-y', 'wine'])
        cls._run_root(['apt-get', 'install', '-y', 'wine32'])
        if shutil.which('wine'):
            print('     ✅ Đã cài Wine')
            return True
        print('⚠️ Không cài được Wine. Cài tay: sudo apt-get install wine wine64')
        return False

    @classmethod
    def ensure_wine_prefix(cls) -> None:
        prefix = Path(cls.wine_prefix())
        env = cls.wine_env()
        env.setdefault('WINEARCH', 'win64')
        prefix.mkdir(parents=True, exist_ok=True)
        if (prefix / 'system.reg').exists():
            return
        print(f'🍷 Khởi tạo Wine prefix {prefix} (Windows 10) ...')
        wine = shutil.which('wine')
        wineboot = shutil.which('wineboot')
        try:
            if wineboot:
                subprocess.run([wineboot, '-i'], env=env, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif wine:
                subprocess.run([wine, 'wineboot', '-i'], env=env, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if wine:
                subprocess.run([wine, 'winecfg', '-v=win10'], env=env, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f'⚠️ wineboot/winecfg: {e}')

    @classmethod
    def _wine_cmd(cls, args: list, timeout: int = 300) -> subprocess.CompletedProcess:
        wine = shutil.which('wine')
        if not wine:
            return subprocess.CompletedProcess(args=['wine', *args], returncode=127)
        env = cls.wine_env()
        env.setdefault('WINEDLLOVERRIDES', 'winemenubuilder.exe=d')
        return subprocess.run([wine, *args], env=env, timeout=timeout)

    @classmethod
    def ensure_webview2(cls) -> None:
        if not cls._download_file(WEBVIEW_URL, WEBVIEW_PATH, 400_000, 'WebView2 Runtime'):
            return
        print('🛠️ Đang cài WebView2 vào Wine (im lặng) ...')
        try:
            cls._wine_cmd([str(WEBVIEW_PATH), '/silent', '/install'], timeout=300)
        except Exception as e:
            print(f'⚠️ WebView2: {e}')

    @classmethod
    def ensure_wine_python(cls) -> bool:
        existing = cls.wine_python_exe()
        if existing:
            return True
        if not shutil.which('wine'):
            return False
        if not cls._download_file(PYTHON_WIN_URL, PYTHON_WIN_PATH, 1_000_000, 'Python 3.10 (Windows/Wine)'):
            print('⚠️ Không tải được Python cho Wine. Bridge 9999 sẽ không chạy.')
            return False
        print('🛠️ Đang cài Python 3.10 vào Wine (C:\\Python310) ...')
        try:
            cls._wine_cmd(
                [
                    str(PYTHON_WIN_PATH),
                    '/quiet',
                    'InstallAllUsers=1',
                    'PrependPath=1',
                    'Include_pip=1',
                    'Include_test=0',
                    r'TargetDir=C:\Python310',
                ],
                timeout=300,
            )
        except Exception as e:
            print(f'⚠️ Cài Python Wine thất bại: {e}')
            return False
        py = cls.wine_python_exe()
        if not py:
            print('⚠️ Cài Python Wine xong nhưng chưa thấy C:\\Python310\\python.exe')
            return False
        print(f'     ✅ Python Wine: {py}')
        return cls._ensure_wine_python_packages(py)

    @classmethod
    def _ensure_wine_python_packages(cls, wine_py: str) -> bool:
        wine = shutil.which('wine')
        if not wine:
            return False
        probe = cls._wine_cmd(
            [cls.linux_to_wine_path(wine_py), '-c', 'import MetaTrader5, flask'],
            timeout=45,
        )
        if probe.returncode == 0:
            return True
        print('📦 Đang pip install MetaTrader5 + flask trong Wine Python ...')
        r = cls._wine_cmd(
            [
                cls.linux_to_wine_path(wine_py),
                '-m',
                'pip',
                'install',
                '--disable-pip-version-check',
                'MetaTrader5',
                'flask',
            ],
            timeout=300,
        )
        if r.returncode != 0:
            print('⚠️ pip Wine chưa cài được MetaTrader5/flask — bridge có thể lỗi.')
            return False
        print('     ✅ Đã cài MetaTrader5 + flask trong Wine')
        return True

    @classmethod
    def install_mt5_via_wine(cls) -> bool:
        if not cls.download_installer():
            print('   Tải thủ công: https://www.exness.com/metatrader-5/')
            return False
        print('🛠️ Đang cài Exness MetaTrader 5 vào Wine (im lặng /auto) ...')
        try:
            cls._wine_cmd([str(INSTALLER_PATH), '/auto'], timeout=420)
        except Exception as e:
            print(f'⚠️ Lỗi chạy bộ cài MT5 qua Wine: {e}')
            return False
        for i in range(60):
            time.sleep(2)
            path = cls.get_mt5_path()
            if path:
                print(f'✅ Cài MT5 xong: {path}')
                return True
            if i in (5, 15, 30):
                print(f'   Đang chờ cài đặt hoàn tất... ({i * 2}s)')
        print('⚠️ Bộ cài đã chạy nhưng chưa thấy terminal64.exe.')
        return False

    @classmethod
    def ensure_linux_stack(cls) -> bool:
        """Ubuntu: tự cài Wine + WebView2 + Exness MT5 + Python Wine nếu thiếu."""
        print('🛠️ Ubuntu chưa có MT5 — đang tự cài Wine + Exness MT5 + Python Wine ...')
        if not cls.ensure_wine_binary():
            return False
        cls.ensure_wine_prefix()
        cls.ensure_webview2()
        if not cls.install_mt5_via_wine():
            return False
        cls.ensure_wine_python()
        path = cls.get_mt5_path()
        if not path:
            return False
        cls.persist_terminal_path(path)
        cls.copy_ea_files(path)
        return True

    @classmethod
    def ensure_installed(cls) -> bool:
        """Nếu chưa có terminal64.exe thì tải và cài Exness MT5 (Windows native / Ubuntu Wine)."""
        path = cls.get_mt5_path()
        if path:
            cls.persist_terminal_path(path)
            cls.copy_ea_files(path)
            print(f"✅ Đã cài Exness MetaTrader 5: {path}")
            if sys.platform != 'win32':
                cls.ensure_wine_python()
            return True

        if sys.platform != 'win32':
            return cls.ensure_linux_stack()

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
            if sys.platform != 'win32':
                cls.ensure_wine_bridge()
            return True

        if sys.platform != 'win32' and not os.environ.get('DISPLAY'):
            print("⚠️ Máy Linux/SSH không có DISPLAY — bỏ qua mở GUI MT5.")
            return False

        path = cls.get_mt5_path()
        if not path:
            print("⚠️ Chưa tìm thấy Exness MetaTrader 5 (terminal64.exe).")
            print("   Tải tại: https://www.exness.com/metatrader-5/")
            if sys.platform == 'win32':
                print("   Hoặc chạy: tools\\exness5setup.exe")
            else:
                print("   ./start.sh sẽ tự cài Wine + MT5. Hoặc chạy tay: bash mt5ubuntu.sh")
            return False

        cls.copy_ea_files(path)

        try:
            print(f"🚀 Đang mở MetaTrader 5: {path}")
            kwargs = {'cwd': os.path.dirname(path)}
            if sys.platform == 'win32':
                kwargs['creationflags'] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
                cmd = [path]
            else:
                wine = shutil.which('wine')
                if not wine:
                    print("⚠️ Chưa có lệnh wine trên PATH. Chạy: bash mt5ubuntu.sh")
                    return False
                kwargs['env'] = cls.wine_env()
                kwargs['start_new_session'] = True
                kwargs['stdout'] = subprocess.DEVNULL
                kwargs['stderr'] = subprocess.DEVNULL
                cmd = [wine, cls.linux_to_wine_path(path)]
            subprocess.Popen(cmd, **kwargs)
            for _ in range(30):
                time.sleep(0.5)
                if cls.is_mt5_process_running():
                    print("✅ Đã khởi động Exness MetaTrader 5.")
                    if sys.platform != 'win32':
                        cls.ensure_wine_bridge()
                    return True
            print("⚠️ Đã gọi MT5 nhưng chưa thấy tiến trình terminal64.exe.")
            if sys.platform != 'win32':
                cls.ensure_wine_bridge()
            return False
        except Exception as e:
            print(f"⚠️ Không mở được MT5: {e}")
            return False

    @classmethod
    def wine_prefix(cls) -> str:
        env_prefix = os.environ.get('WINEPREFIX', '').strip()
        if env_prefix and os.path.isdir(env_prefix):
            return env_prefix
        mt5_prefix = os.path.expanduser('~/.mt5')
        if os.path.isdir(mt5_prefix):
            return mt5_prefix
        return os.path.expanduser('~/.wine')

    @classmethod
    def wine_env(cls) -> dict:
        env = os.environ.copy()
        env.setdefault('DISPLAY', ':0')
        env['WINEPREFIX'] = cls.wine_prefix()
        env.setdefault('WINEDEBUG', '-all')
        env.setdefault('WINEDLLOVERRIDES', 'winemenubuilder.exe=d')
        return env

    @classmethod
    def linux_to_wine_path(cls, linux_path: str) -> str:
        prefix = cls.wine_prefix()
        drive_c = os.path.join(prefix, 'drive_c')
        abs_path = os.path.abspath(linux_path)
        if abs_path.startswith(drive_c + os.sep) or abs_path == drive_c:
            rel = abs_path[len(drive_c):].lstrip('/').replace('/', '\\')
            return 'C:\\' + rel
        return 'Z:' + abs_path

    @classmethod
    def wine_python_exe(cls) -> str:
        prefix = cls.wine_prefix()
        for name in ('Python310', 'Python312', 'Python311', 'Python39', 'Python38'):
            candidate = os.path.join(prefix, 'drive_c', name, 'python.exe')
            if os.path.isfile(candidate):
                return candidate
        return None

    @classmethod
    def is_wine_bridge_running(cls) -> bool:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.2)
            res = sock.connect_ex(('127.0.0.1', 9999))
            sock.close()
            return res == 0
        except Exception:
            return False

    @classmethod
    def ensure_wine_bridge(cls) -> bool:
        """Linux: Django nói chuyện với MT5 qua Wine Python bridge cổng 9999."""
        if sys.platform == 'win32':
            return True
        if cls.is_wine_bridge_running():
            print('✅ Wine Bridge đã chạy tại http://127.0.0.1:9999/')
            return True

        wine = shutil.which('wine')
        wine_py = cls.wine_python_exe()
        bridge = PROJECT_ROOT / 'deploy' / 'mt5_wine_bridge.py'
        if not wine:
            print('⚠️ Chưa có Wine — không start được bridge 9999. Chạy: bash mt5ubuntu.sh')
            return False
        if not wine_py:
            print('⚠️ Chưa có Python trong Wine (C:\\Python310\\python.exe).')
            print('   Cài Python 3.10 vào prefix WINEPREFIX=~/.mt5 rồi chạy lại ./start.sh')
            return False
        if not bridge.exists():
            print('⚠️ Thiếu deploy/mt5_wine_bridge.py')
            return False

        log_dir = PROJECT_ROOT / 'logs'
        log_dir.mkdir(exist_ok=True)
        log_file = open(log_dir / 'mt5_wine_bridge.log', 'ab')
        print(f'🚀 Đang mở Wine Bridge cổng 9999 ...')
        subprocess.Popen(
            [wine, cls.linux_to_wine_path(wine_py), '-u', cls.linux_to_wine_path(str(bridge))],
            env=cls.wine_env(),
            cwd=str(PROJECT_ROOT),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        for _ in range(40):
            time.sleep(0.5)
            if cls.is_wine_bridge_running():
                print('✅ Wine Bridge sẵn sàng (cổng 9999).')
                return True
        print('⚠️ Đã gọi Wine Bridge nhưng cổng 9999 chưa mở. Xem logs/mt5_wine_bridge.log')
        return False

    @classmethod
    def _snapshot_from_wine_bridge(cls, timeout_sec: float = 2.0) -> dict:
        """Đọc login / Algo Trading từ Wine Bridge (Linux)."""
        empty = {
            'ready': False,
            'logged_in': False,
            'trade_allowed': False,
            'connected': False,
            'account': None,
            'error': None,
        }
        if not cls.is_wine_bridge_running():
            empty['error'] = 'Wine Bridge cổng 9999 chưa mở'
            return empty
        try:
            import requests
            url = os.environ.get('MT5_BRIDGE_URL', 'http://127.0.0.1:9999').rstrip('/')
            health = requests.get(f'{url}/health', timeout=timeout_sec)
            h = health.json() if health.ok else {}
            term = h.get('terminal_info') or {}
            acc_res = requests.get(f'{url}/account_info', timeout=timeout_sec)
            acc_body = acc_res.json() if acc_res.ok else {}
            acc = acc_body.get('account_info') if acc_body.get('success') else None
            account = None
            logged_in = False
            if acc and acc.get('login'):
                logged_in = True
                account = {
                    'login': int(acc['login']),
                    'server': str(acc.get('server') or ''),
                    'balance': float(acc.get('balance') or 0),
                }
            return {
                'ready': bool(h.get('initialized') or logged_in),
                'logged_in': logged_in,
                'trade_allowed': bool(
                    term.get('trade_allowed') or (acc and acc.get('trade_allowed'))
                ),
                'connected': bool(term.get('connected')),
                'account': account,
                'error': None if logged_in else (acc_body.get('error') or h.get('message')),
            }
        except Exception as e:
            empty['error'] = str(e)
            return empty

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
            snap = None
            try:
                from apps.trading.mt5_session import MT5NativeSession
                if MT5NativeSession.available():
                    snap = MT5NativeSession.terminal_snapshot()
            except ImportError:
                error = 'Chưa cài gói Python MetaTrader5 (pip install MetaTrader5).'
            except Exception as e:
                error = str(e)

            # Linux: native MetaTrader5 IPC không có — đọc Wine Bridge cổng 9999.
            if (not snap or not snap.get('logged_in')) and sys.platform != 'win32':
                snap = cls._snapshot_from_wine_bridge(timeout_sec=max(0.8, timeout_ms / 1000.0))

            if snap:
                api_ok = bool(snap.get('ready'))
                logged_in = bool(snap.get('logged_in'))
                algo_trading = bool(snap.get('trade_allowed'))
                account = snap.get('account')
                error = snap.get('error')

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

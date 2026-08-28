import os
import sys
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class MT5Launcher:
    """
    Tự động phát hiện và khởi động phần mềm Exness MT5 Terminal cùng lúc khi chạy dự án.
    """

    COMMON_PATHS = [
        # 1. Project local portable folder (nếu người dùng copy MT5 vào source code)
        str(Path(__file__).resolve().parent.parent.parent / 'tools' / 'mt5' / 'terminal64.exe'),
        str(Path(__file__).resolve().parent.parent.parent / 'mt5' / 'terminal64.exe'),
        
        # 2. Windows Standard Installation Paths
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files\Exness MetaTrader 5\terminal64.exe",
        r"C:\Program Files\Exness MT5\terminal64.exe",
        r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        r"C:\Program Files (x86)\Exness MetaTrader 5\terminal64.exe",
        
        # 3. Linux Wine Default Paths
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
            if os.path.exists(path):
                return path
        return None

    @classmethod
    def is_mt5_process_running(cls) -> bool:
        """Kiểm tra tiến trình terminal64.exe có đang chạy không."""
        try:
            if sys.platform == 'win32':
                output = subprocess.check_output(['tasklist'], encoding='utf-8', errors='ignore')
                return 'terminal64.exe' in output.lower() or 'terminal.exe' in output.lower()
            else:
                output = subprocess.check_output(['pgrep', '-f', 'terminal64.exe'], encoding='utf-8', errors='ignore')
                return bool(output.strip())
        except Exception:
            return False

    @classmethod
    def ensure_terminal_running(cls) -> bool:
        """
        Headless Mode: Không bao giờ bật GUI terminal trên Linux/SSH Server.
        """
        return False


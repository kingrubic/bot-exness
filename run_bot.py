import os
import sys
import time
import threading
from pathlib import Path

# Add local packages folder to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'packages'))

import django
from django.core.management import execute_from_command_line

# Setup Django Environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'exness_project.settings')
django.setup()

from apps.accounts.models import WalletAccount
from apps.trading.execution_engine import ExecutionEngine

def run_trading_bot_worker():
    """Vòng lặp ngầm của Trading Bot tự động quét nến, phân tích và khớp lệnh."""
    print("🚀 [BOT WORKER] Khởi động luồng quét tự động Exness...")
    time.sleep(3) # Wait for server to start
    
    while True:
        try:
            ExecutionEngine.run_full_trading_cycle()
        except Exception as e:
            print(f"⚠️ [BOT WORKER ERROR] Lỗi trong chu kỳ giao dịch: {e}")
        time.sleep(4) # Run every 4 seconds

def main():
    print("=" * 70)
    print("⚡ KHỞI ĐỘNG HỆ THỐNG EXNESS AUTO-TRADE DJANGO PLATFORM ⚡")
    print("=" * 70)

    # 1. Run migrations
    print("📦 Đang kiểm tra & áp dụng Database Migrations...")
    execute_from_command_line(['manage.py', 'makemigrations', 'accounts', 'symbols', 'analysis', 'plans', 'trading'])
    execute_from_command_line(['manage.py', 'migrate'])

    # 2. Start background trading bot thread
    bot_thread = threading.Thread(target=run_trading_bot_worker, daemon=True)
    bot_thread.start()

    # 3. Start Django Server with CLI port or default 0.0.0.0:8000
    addrport = sys.argv[1] if len(sys.argv) > 1 else '0.0.0.0:8000'
    if ':' not in addrport and addrport.isdigit():
        addrport = f'0.0.0.0:{addrport}'
    
    port_display = addrport.split(':')[-1]
    print(f"\n🌐 Web Dashboard đã sẵn sàng tại: http://localhost:{port_display}/")
    print(f"💼 Trang Quản Trị Admin tại:       http://localhost:{port_display}/admin-panel/\n")
    
    execute_from_command_line(['manage.py', 'runserver', addrport, '--noreload'])

if __name__ == '__main__':
    main()

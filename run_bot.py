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
    time.sleep(2) # Wait for server to start
    
    from django.db import close_old_connections
    cycle_counter = 0
    while True:
        try:
            close_old_connections()
            # 1. Update live price from MT5 and position floating PnLs every 150ms
            ExecutionEngine.sync_symbol_prices_from_mt5()
            ExecutionEngine.update_positions_and_pnl()

            # 2. Run full strategy & plan check every 20 ticks (~3s)
            cycle_counter += 1
            if cycle_counter % 20 == 0:
                ExecutionEngine.run_full_trading_cycle()

            # 3. Sync MT5 closed history every 60 cycles (~9s)
            if cycle_counter >= 60:
                cycle_counter = 0
                for w in WalletAccount.objects.filter(is_active=True, mt5_login__isnull=False):
                    try:
                        from apps.trading.mt5_connector import ExnessMT5Connector
                        connector = ExnessMT5Connector(login=w.mt5_login, password=w.mt5_password, server=w.mt5_server)
                        if connector.connect():
                            connector.sync_history_from_mt5(w)
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ [BOT WORKER ERROR] Lỗi trong chu kỳ giao dịch: {e}")
        finally:
            close_old_connections()
        time.sleep(0.15) # Blazing fast 150ms background loop

def main():
    print("=" * 70)
    print("⚡ KHỞI ĐỘNG HỆ THỐNG EXNESS AUTO-TRADE DJANGO PLATFORM ⚡")
    print("=" * 70)

    # 1. Run migrations & ensure Master Data exists
    print("📦 Đang kiểm tra & áp dụng Database Migrations...")
    execute_from_command_line(['manage.py', 'makemigrations', 'accounts', 'symbols', 'analysis', 'plans', 'trading'])
    execute_from_command_line(['manage.py', 'migrate'])

    from apps.accounts.models import ExnessServerMaster
    if ExnessServerMaster.objects.count() == 0:
        from apps.core.seed_data import run_seed
        print("🌱 Đang khởi tạo Master Data cho sàn Exness...")
        run_seed()

    # 2. Start background trading bot thread
    bot_thread = threading.Thread(target=run_trading_bot_worker, daemon=True)
    bot_thread.start()

    # 5. Start Django Server with CLI port or default 0.0.0.0:8888
    addrport = sys.argv[1] if len(sys.argv) > 1 else '0.0.0.0:8888'
    if ':' not in addrport and addrport.isdigit():
        addrport = f'0.0.0.0:{addrport}'
    
    port_display = addrport.split(':')[-1]
    print(f"\n🌐 Web Dashboard đã sẵn sàng tại: http://localhost:{port_display}/")
    print(f"💼 Trang Quản Trị Admin tại:       http://localhost:{port_display}/admin-panel/\n")
    
    execute_from_command_line(['manage.py', 'runserver', addrport, '--noreload'])

if __name__ == '__main__':
    main()

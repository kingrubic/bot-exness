import time
from django.core.management.base import BaseCommand
from apps.trading.execution_engine import ExecutionEngine

class Command(BaseCommand):
    help = 'Khởi động Bot Worker tự động quét giá và khớp lệnh Exness ngầm'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=4, help='Chu kỳ quét giá (giây)')

    def handle(self, *args, **options):
        interval = options['interval']
        self.stdout.write(self.style.SUCCESS("🚀 [BOT WORKER] Bắt đầu luồng tự động quét giá & khớp lệnh Exness..."))
        
        while True:
            try:
                ExecutionEngine.run_full_trading_cycle()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"⚠️ [BOT ERROR] Lỗi trong chu kỳ giao dịch: {e}"))
            time.sleep(interval)

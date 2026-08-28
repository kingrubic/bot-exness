from django.db import models
from django.utils import timezone
import json

class WalletAccount(models.Model):
    ACCOUNT_TYPES = [
        ('REAL', 'Tài Khoản Real'),
        ('DEMO', 'Tài Khoản Demo'),
        ('SIMULATION', 'Tài Khoản Giả Lập Sandbox'),
    ]

    BOT_STATUSES = [
        ('RUNNING', 'Đang Hoạt Động (Auto Trade)'),
        ('PAUSED', 'Tạm Dừng'),
        ('STOPPED', 'Đã Dừng Hẳn'),
    ]

    name = models.CharField(max_length=150, verbose_name="Tên Ví / Tài Khoản")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES, default='DEMO', verbose_name="Loại Tài Khoản")
    mt5_login = models.CharField(max_length=64, verbose_name="Số Tài Khoản MT5")
    mt5_password = models.CharField(max_length=128, blank=True, verbose_name="Mật Khẩu MT5")
    mt5_server = models.CharField(max_length=100, default="Exness-MT5Real", verbose_name="Server Exness")
    currency = models.CharField(max_length=10, default="USD", verbose_name="Tiền Tệ")
    leverage = models.IntegerField(default=2000, verbose_name="Đòn Bẩy (1:X)")
    
    # Financial Balances
    initial_balance = models.DecimalField(max_digits=15, decimal_places=2, default=10000.00, verbose_name="Số Dư Ban Đầu")
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=10000.00, verbose_name="Số Dư Hiện Tại (Balance)")
    equity = models.DecimalField(max_digits=15, decimal_places=2, default=10000.00, verbose_name="Vốn Khả Dụng (Equity)")
    floating_pnl = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Lãi/Lỗ Tạm Tính (Floating PnL)")
    today_pnl = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Lãi/Lỗ Hôm Nay")
    total_profit = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Tổng Lợi Nhuận Đã Chốt")
    
    # Performance Statistics
    win_rate = models.FloatField(default=0.0, verbose_name="Tỷ Lệ Thắng (%)")
    total_trades = models.IntegerField(default=0, verbose_name="Tổng Số Lệnh")
    winning_trades = models.IntegerField(default=0, verbose_name="Số Lệnh Thắng")
    losing_trades = models.IntegerField(default=0, verbose_name="Số Lệnh Thua")
    profit_factor = models.FloatField(default=1.5, verbose_name="Profit Factor")
    max_drawdown = models.FloatField(default=0.0, verbose_name="Max Drawdown (%)")
    
    # Risk Management Per Wallet
    risk_percent = models.FloatField(default=1.5, verbose_name="% Rủi Ro Mỗi Lệnh")
    max_daily_loss_percent = models.FloatField(default=4.0, verbose_name="% Giới Hạn Lỗ Tối Đa Trong Ngày")
    max_open_trades = models.IntegerField(default=5, verbose_name="Số Lệnh Mở Tối Đa")
    
    # Active Pairs configuration for this wallet
    # JSON list of allowed symbols e.g. ["XAUUSD", "EURUSD"]
    allowed_symbols_json = models.TextField(default='["XAUUSD"]', verbose_name="Danh Sách Cặp Cho Phép")
    
    # Execution & Status
    is_active = models.BooleanField(default=True, verbose_name="Kích Hoạt")
    bot_status = models.CharField(max_length=20, choices=BOT_STATUSES, default='RUNNING', verbose_name="Trạng Thái Bot")
    
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Ngày Tạo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Cập Nhật Lần Cuối")

    class Meta:
        verbose_name = "Ví Exness"
        verbose_name_plural = "Danh Sách Ví Exness"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.account_type}) - #{self.mt5_login}"

    @property
    def allowed_symbols(self):
        try:
            return json.loads(self.allowed_symbols_json)
        except Exception:
            return ["XAUUSD"]

    def set_allowed_symbols(self, symbols_list):
        self.allowed_symbols_json = json.dumps(symbols_list)

    def calculate_metrics(self):
        """Tính toán lại winrate và số liệu hiệu suất."""
        if self.total_trades > 0:
            self.win_rate = round((self.winning_trades / self.total_trades) * 100, 1)
        else:
            self.win_rate = 0.0
        from decimal import Decimal
        self.equity = Decimal(str(self.balance)) + Decimal(str(self.floating_pnl))

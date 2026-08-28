from django.db import models
from django.utils import timezone
import json

class WalletAccount(models.Model):
    ACCOUNT_TYPES = [
        ('REAL', 'Real'),
        ('DEMO', 'Demo'),
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
    
    # Financial Capital & Metrics (Chỉ lưu vốn khi connect, số dư và equity lấy động)
    capital = models.DecimalField(max_digits=15, decimal_places=2, default=1000.00, verbose_name="Vốn Khi Kết Nối (Capital USD)")
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
    def initial_balance(self):
        return self.capital

    @initial_balance.setter
    def initial_balance(self, val):
        self.capital = val

    @property
    def balance(self):
        """Số dư tính động theo sàn hoặc theo Vốn + Tổng lãi đã chốt."""
        if hasattr(self, '_live_balance') and self._live_balance is not None:
            return self._live_balance
        from decimal import Decimal
        return Decimal(str(self.capital)) + Decimal(str(self.total_profit))

    @balance.setter
    def balance(self, val):
        self._live_balance = val

    @property
    def equity(self):
        """Vốn khả dụng tính động theo Số dư + Lãi/lỗ thả nổi."""
        if hasattr(self, '_live_equity') and self._live_equity is not None:
            return self._live_equity
        from decimal import Decimal
        return Decimal(str(self.balance)) + Decimal(str(self.floating_pnl))

    @equity.setter
    def equity(self, val):
        self._live_equity = val

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


class ExnessServerMaster(models.Model):
    SERVER_TYPES = [
        ('REAL', 'Real'),
        ('DEMO', 'Demo'),
    ]

    server_name = models.CharField(max_length=100, unique=True, verbose_name="Tên Server Exness")
    server_type = models.CharField(max_length=20, choices=SERVER_TYPES, default='REAL', verbose_name="Loại Máy Chủ")
    description = models.CharField(max_length=200, blank=True, default='', verbose_name="Mô Tả / Ghi Chú")
    is_active = models.BooleanField(default=True, verbose_name="Đang Hoạt Động")
    order = models.IntegerField(default=0, verbose_name="Thứ Tự Sắp Xếp")

    class Meta:
        verbose_name = "Server Exness (Master Data)"
        verbose_name_plural = "Danh Sách Server Exness (Master Data)"
        ordering = ['order', 'server_name']

    def __str__(self):
        return f"{self.server_name} ({self.get_server_type_display()})"


from django.db import models
from django.utils import timezone
from decimal import Decimal
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
    balance_db = models.DecimalField(max_digits=15, decimal_places=2, default=1000.00, verbose_name="Số Dư Thực Tế (Balance USD)")
    equity_db = models.DecimalField(max_digits=15, decimal_places=2, default=1000.00, verbose_name="Vốn Khả Dụng (Equity USD)")
    floating_pnl = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Lãi/Lỗ Tạm Tính (Floating PnL)")
    margin = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Ký Quỹ Đã Dùng (Margin USD)")
    margin_free = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Ký Quỹ Khả Dụng (Free Margin USD)")
    margin_level = models.FloatField(default=0.0, verbose_name="Mức Ký Quỹ % (Margin Level %)")
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
    default_lot_size = models.FloatField(default=0.01, verbose_name="Khối Lượng Đánh Mặc Định (Lot)")
    risk_percent = models.FloatField(default=1.5, verbose_name="% Rủi Ro Mỗi Lệnh")
    max_daily_loss_percent = models.FloatField(default=4.0, verbose_name="% Giới Hạn Lỗ Tối Đa Trong Ngày")
    max_open_trades = models.IntegerField(default=100, verbose_name="Số Lệnh Mở Tối Đa")
    min_take_profit_usd = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('1.00'),
        verbose_name="Số Tiền Min Chốt Lời (USD)"
    )
    
    # Active Pairs configuration for this wallet
    # JSON list of allowed symbols e.g. ["XAUUSD", "EURUSD"]
    allowed_symbols_json = models.TextField(
        default='["XAUUSD", "BTCUSD", "ETHUSD"]',
        verbose_name="Danh Sách Cặp Cho Phép",
    )
    
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
        """Số dư MT5 đã lưu. $0 sau thanh lý phải hiện 0, không lấy lại capital."""
        if hasattr(self, '_live_balance') and self._live_balance is not None:
            return self._live_balance
        if self.balance_db is not None:
            return self.balance_db
        return Decimal('0.00')

    @balance.setter
    def balance(self, val):
        self._live_balance = val
        self.balance_db = val

    @property
    def equity(self):
        """Equity MT5 đã lưu. $0 sau thanh lý phải hiện 0."""
        if hasattr(self, '_live_equity') and self._live_equity is not None:
            return self._live_equity
        if self.equity_db is not None:
            return self.equity_db
        return Decimal('0.00')

    @equity.setter
    def equity(self, val):
        self._live_equity = val
        self.equity_db = val

    @property
    def allowed_symbols(self):
        try:
            data = json.loads(self.allowed_symbols_json or '[]')
            if isinstance(data, list):
                return [str(s).strip() for s in data if str(s).strip()]
        except Exception:
            pass
        return []

    @property
    def leverage_display(self):
        return f"1:{self.leverage}"

    def set_allowed_symbols(self, symbols_list):
        self.allowed_symbols_json = json.dumps(symbols_list)

    def get_today_pnl(self):
        """Lãi/lỗ đã đóng hôm nay: ưu tiên tổng deal MT5 trong ngày, fallback DB nếu terminal không sẵn sàng."""
        from decimal import Decimal
        from django.db.models import Sum
        try:
            from apps.trading.mt5_session import MT5NativeSession
            if self.mt5_login and MT5NativeSession.available():
                acc = MT5NativeSession.account()
                if acc and str(acc.login) == str(self.mt5_login):
                    snap = MT5NativeSession.today_realized_pnl()
                    if snap.get('ok'):
                        return Decimal(str(snap['all']))
        except Exception:
            pass
        today = timezone.localdate()
        agg = self.trade_history.filter(closed_at__date=today).aggregate(tot=Sum('pnl'))
        return Decimal(str(round(agg['tot'], 2))) if agg['tot'] is not None else Decimal('0.00')

    def calculate_metrics(self):
        """Tính toán lại winrate, lãi/lỗ hôm nay và số liệu hiệu suất 100% từ lịch sử Exness MT5."""
        from decimal import Decimal
        from django.db.models import Sum
        if self.pk:
            all_hist = self.trade_history.all()
            tot = all_hist.count()
            wins = all_hist.filter(pnl__gt=0).count()
            losses = all_hist.filter(pnl__lt=0).count()
            self.total_trades = tot
            self.winning_trades = wins
            self.losing_trades = losses
            self.win_rate = round((wins / tot) * 100, 1) if tot > 0 else 0.0
            
            tot_pnl = all_hist.aggregate(s=Sum('pnl'))['s']
            self.total_profit = Decimal(str(round(tot_pnl, 2))) if tot_pnl is not None else Decimal('0.00')
            self.today_pnl = self.get_today_pnl()

    def get_performance_breakdown(self):
        """Tính toán tách bạch toàn bộ chỉ số hiệu suất giữa Lệnh BOT và Lệnh USER."""
        from django.db.models import Sum
        
        def _calc_stats(qs):
            total = qs.count()
            wins = qs.filter(pnl__gt=0).count()
            losses = qs.filter(pnl__lt=0).count()
            winrate = round((wins / total) * 100, 1) if total > 0 else 0.0
            
            tot_pnl = float(qs.aggregate(s=Sum('pnl'))['s'] or 0.0)
            gross_win = float(qs.filter(pnl__gt=0).aggregate(s=Sum('pnl'))['s'] or 0.0)
            gross_loss = abs(float(qs.filter(pnl__lt=0).aggregate(s=Sum('pnl'))['s'] or 0.0))
            
            pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win > 0 else 1.0)
            tot_vol = round(float(qs.aggregate(v=Sum('lot_size'))['v'] or 0.0), 2)
            
            today = timezone.localdate()
            today_pnl = float(qs.filter(closed_at__date=today).aggregate(s=Sum('pnl'))['s'] or 0.0)
            
            return {
                'total_trades': total,
                'winning_trades': wins,
                'losing_trades': losses,
                'win_rate': winrate,
                'total_profit': round(tot_pnl, 2),
                'gross_profit': round(gross_win, 2),
                'gross_loss': round(gross_loss, 2),
                'profit_factor': pf,
                'total_volume': tot_vol,
                'today_pnl': round(today_pnl, 2),
            }

        all_history = self.trade_history.all()
        bot_history = all_history.filter(source='BOT')
        user_history = all_history.filter(source='USER')

        return {
            'all': _calc_stats(all_history),
            'bot': _calc_stats(bot_history),
            'user': _calc_stats(user_history),
        }


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


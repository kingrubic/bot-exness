from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.plans.models import TradingPlan

class Position(models.Model):
    POSITION_TYPES = [
        ('BUY', 'BUY (Mua)'),
        ('SELL', 'SELL (Bán)'),
    ]

    ORDER_SOURCES = [
        ('BOT', 'Bot (Tự Động)'),
        ('USER', 'User (Người Dùng)'),
    ]

    wallet = models.ForeignKey(WalletAccount, on_delete=models.CASCADE, related_name='positions', verbose_name="Ví")
    plan = models.ForeignKey(TradingPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name='positions', verbose_name="Plan Gốc")
    ticket = models.CharField(max_length=64, unique=True, verbose_name="Ticket ID (MT5)")
    
    symbol = models.CharField(max_length=30, verbose_name="Mã Cặp")
    position_type = models.CharField(max_length=10, choices=POSITION_TYPES, verbose_name="Loại Lệnh")
    lot_size = models.FloatField(default=0.1, verbose_name="Khối Lượng (Lot)")
    
    open_price = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Giá Khớp Lệnh (Entry)")
    current_price = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Giá Thị Trường Hiện Tại")
    stop_loss = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Cắt Lỗ (SL)")
    take_profit = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Chốt Lời (TP)")
    
    floating_pnl = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Lãi/Lỗ Tạm Tính ($)")
    floating_pips = models.FloatField(default=0.0, verbose_name="Số Pips Tạm Tính")
    commission = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), null=True, blank=True, verbose_name="Phí Hoa Hồng ($)")
    swap = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), null=True, blank=True, verbose_name="Phí Qua Đêm ($)")
    
    source = models.CharField(max_length=10, choices=ORDER_SOURCES, default='BOT', verbose_name="Nguồn Lệnh (Bot/User)")
    magic = models.IntegerField(default=0, verbose_name="Magic Number")
    comment = models.CharField(max_length=150, blank=True, default='', verbose_name="Comment / Ghi Chú MT5")

    is_trailing = models.BooleanField(default=True, verbose_name="Bật Trailing Stop")
    is_breakeven_set = models.BooleanField(default=False, verbose_name="Đã Dời Về Hòa Vốn (BE)")
    highest_price = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Đỉnh Giá Cao Nhất Đạt Được")
    lowest_price = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Đáy Giá Thấp Nhất Đạt Được")
    
    opened_at = models.DateTimeField(default=timezone.now, verbose_name="Thời Điểm Mở")
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def net_floating_pnl(self) -> Decimal:
        """Lợi nhuận ròng thực nhận sau khi trừ phí hoa hồng và phí qua đêm."""
        f_pnl = Decimal(str(self.floating_pnl or 0.0))
        c_val = Decimal(str(self.commission or 0.0))
        s_val = Decimal(str(self.swap or 0.0))
        return f_pnl + c_val + s_val

    class Meta:
        verbose_name = "Vị Thế Đang Mở"
        verbose_name_plural = "Danh Sách Lệnh Đang Mở"
        ordering = ['-opened_at']

    def __str__(self):
        return f"Position #{self.ticket} [{self.wallet.name}] {self.symbol} {self.position_type} {self.lot_size} lot (PnL: ${self.floating_pnl})"


class TradeHistory(models.Model):
    CLOSE_REASONS = [
        ('TP_HIT', 'Chạm Take Profit (TP Hit)'),
        ('SL_HIT', 'Cắt Lỗ Tự Động (SL Hit)'),
        ('TRAILING_STOP', 'Chạm Trailing Stop'),
        ('TRAILING_TP', 'Chốt Lời Thoái Lui Đỉnh (Trailing TP)'),
        ('TREND_REVERSAL_SL', 'Cắt Lỗ Khi Đảo Chiều Trend'),
        ('MAX_DRAWDOWN_SL', 'Cắt Lỗ Ngưỡng An Toàn Tối Đa'),
        ('MARGIN_SAFETY_SL', 'Cắt Lỗ Cứu Ký Quỹ Ví (Margin Safety)'),
        ('TREND_REVERSAL', 'Chốt Lời Khi Đảo Chiều Trend'),
        ('USER_AUTO_TP', 'Bot Chốt Lời Cho User (User Auto TP)'),
        ('MANUAL_CLOSE', 'Đóng Thủ Công (Manual Close)'),
        ('MAX_DAILY_DD', 'Dừng Do Chạm Max Daily Loss'),
    ]

    ORDER_SOURCES = [
        ('BOT', 'Bot (Tự Động)'),
        ('USER', 'User (Người Dùng)'),
    ]

    wallet = models.ForeignKey(WalletAccount, on_delete=models.CASCADE, related_name='trade_history', verbose_name="Ví")
    ticket = models.CharField(max_length=64, verbose_name="Ticket ID (MT5)")
    symbol = models.CharField(max_length=30, verbose_name="Mã Cặp")
    position_type = models.CharField(max_length=10, choices=Position.POSITION_TYPES, verbose_name="Loại Lệnh")
    lot_size = models.FloatField(verbose_name="Khối Lượng (Lot)")
    
    open_price = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Giá Vào")
    close_price = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Giá Đóng")
    stop_loss = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Stop Loss")
    take_profit = models.DecimalField(max_digits=15, decimal_places=5, null=True, blank=True, verbose_name="Take Profit")
    
    pnl = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Lãi/Lỗ Thực Tế ($)")
    commission = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), null=True, blank=True, verbose_name="Phí Hoa Hồng ($)")
    swap = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), null=True, blank=True, verbose_name="Phí Qua Đêm ($)")
    pips = models.FloatField(default=0.0, verbose_name="Số Pips Lời/Lỗ")
    close_reason = models.CharField(max_length=30, choices=CLOSE_REASONS, default='TP_HIT', verbose_name="Lý Do Đóng Lệnh")
    is_win = models.BooleanField(default=True, verbose_name="Lệnh Thắng")
    
    source = models.CharField(max_length=10, choices=ORDER_SOURCES, default='BOT', verbose_name="Nguồn Lệnh (Bot/User)")
    magic = models.IntegerField(default=0, verbose_name="Magic Number")
    comment = models.CharField(max_length=150, blank=True, default='', verbose_name="Comment / Ghi Chú MT5")

    opened_at = models.DateTimeField(verbose_name="Thời Điểm Mở")
    closed_at = models.DateTimeField(default=timezone.now, verbose_name="Thời Điểm Đóng")

    class Meta:
        verbose_name = "Lịch Sử Giao Dịch"
        verbose_name_plural = "Lịch Sử Giao Dịch Đã Đóng"
        ordering = ['-closed_at']
        unique_together = ('wallet', 'ticket')

    def __str__(self):
        return f"History #{self.ticket} [{self.wallet.name}] {self.symbol} {self.position_type} -> PnL: ${self.pnl}"


class BotLog(models.Model):
    LOG_LEVELS = [
        ('INFO', 'Thông Tin (INFO)'),
        ('WARNING', 'Cảnh Báo (WARNING)'),
        ('ERROR', 'Lỗi (ERROR)'),
        ('CRITICAL', 'Nghiêm Trọng (CRITICAL)'),
    ]

    LOG_CATEGORIES = [
        ('SYSTEM', 'Hệ Thống (System)'),
        ('CONNECTION', 'Kết Nối Sàn/MT5 (Broker Connection)'),
        ('ANALYSIS', 'Phân Tích Nến (Technical Analysis)'),
        ('PLAN', 'Kế Hoạch Giao Dịch (Trading Plan)'),
        ('EXECUTION', 'Khớp Lệnh & Khối Lượng (Trade Execution)'),
        ('RISK', 'Quản Trị Rủi Ro (Risk Management)'),
    ]

    level = models.CharField(max_length=15, choices=LOG_LEVELS, default='INFO', db_index=True)
    category = models.CharField(max_length=20, choices=LOG_CATEGORIES, default='SYSTEM', db_index=True)
    wallet = models.ForeignKey(WalletAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name='logs', verbose_name="Ví Liên Quan")
    symbol = models.CharField(max_length=30, blank=True, default='', verbose_name="Mã Cặp")
    message = models.TextField(verbose_name="Nội Dung Lỗi / Thông Điệp")
    traceback = models.TextField(blank=True, default='', verbose_name="Chi Tiết Lỗi Kỹ Thuật (Traceback)")
    is_resolved = models.BooleanField(default=False, verbose_name="Đã Xử Lý")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Thời Điểm Ghi Nhận")

    class Meta:
        verbose_name = "Nhật Ký & Cảnh Báo Bot"
        verbose_name_plural = "Nhật Ký & Cảnh Báo Bot"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.category}: {self.message[:60]}"
    
    @classmethod
    def log(cls, level='INFO', category='SYSTEM', message='', traceback='', wallet=None, symbol=''):
        """Lưu log Bot vào Database và phát sóng tức thì qua WebSocket."""
        try:
            log_obj = cls.objects.create(
                level=level,
                category=category,
                message=str(message),
                traceback=str(traceback) if traceback else '',
                wallet=wallet,
                symbol=symbol
            )
            return log_obj
        except Exception as e:
            print(f"[BOT LOGGER DB ERROR] Không thể lưu log vào DB: {e}")
            return None


class CodeLog(models.Model):
    LOG_LEVELS = [
        ('ERROR', 'Lỗi (ERROR)'),
        ('CRITICAL', 'Nghiêm Trọng (CRITICAL)'),
        ('WARNING', 'Cảnh Báo (WARNING)'),
        ('INFO', 'Thông Tin (INFO)'),
    ]

    level = models.CharField(max_length=15, choices=LOG_LEVELS, default='ERROR', db_index=True)
    module = models.CharField(max_length=255, blank=True, default='', verbose_name="Module / Tệp Gặp Lỗi")
    line_number = models.IntegerField(null=True, blank=True, verbose_name="Số Dòng")
    exception_type = models.CharField(max_length=100, blank=True, default='', verbose_name="Loại Ngoại Lệ")
    message = models.TextField(verbose_name="Nội Dung Lỗi Code")
    traceback = models.TextField(blank=True, default='', verbose_name="Chi Tiết Traceback Kỹ Thuật")
    request_path = models.CharField(max_length=255, blank=True, default='', verbose_name="URL / API Endpoint")
    request_method = models.CharField(max_length=10, blank=True, default='', verbose_name="Method (GET/POST)")
    is_resolved = models.BooleanField(default=False, verbose_name="Đã Xử Lý")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Thời Điểm Ghi Nhận")

    class Meta:
        verbose_name = "Nhật Ký & Báo Lỗi Code"
        verbose_name_plural = "Nhật Ký & Báo Lỗi Code"
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.exception_type or self.module}: {self.message[:60]}"

    @classmethod
    def log_exception(cls, exception=None, request=None, level='ERROR', custom_message='', module='', line_number=None, traceback_str=''):
        """Tự động ghi nhận lỗi code/exception vào Database."""
        import traceback as tb_module
        
        if not traceback_str and exception:
            traceback_str = tb_module.format_exc()

        exc_type = type(exception).__name__ if exception else 'SystemError'
        msg = custom_message or (str(exception) if exception else 'Lỗi thực thi mã nguồn')

        mod_name = module
        line_no = line_number

        if not mod_name and exception and hasattr(exception, '__traceback__') and exception.__traceback__:
            tb = exception.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            frame = tb.tb_frame
            mod_name = frame.f_code.co_filename
            line_no = tb.tb_lineno

        req_path = ''
        req_method = ''
        if request:
            req_path = getattr(request, 'path', '')
            req_method = getattr(request, 'method', '')

        try:
            return cls.objects.create(
                level=level,
                module=mod_name,
                line_number=line_no,
                exception_type=exc_type,
                message=msg,
                traceback=traceback_str or '',
                request_path=req_path,
                request_method=req_method
            )
        except Exception as e:
            print(f"[CODE LOGGER DB ERROR] Không thể lưu code log: {e}")
            return None




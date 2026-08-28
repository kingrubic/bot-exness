from django.db import models
from django.utils import timezone
from apps.accounts.models import WalletAccount

class TradingPlan(models.Model):
    DIRECTION_CHOICES = [
        ('BUY', 'Mua (BUY / Long)'),
        ('SELL', 'Bán (SELL / Short)'),
    ]

    STATUS_CHOICES = [
        ('ANALYZING', 'Đang Phân Tích'),
        ('PENDING_TRIGGER', 'Chờ Khớp Vùng Entry'),
        ('EXECUTING', 'Đang Thực Thi Vào Lệnh'),
        ('COMPLETED', 'Đã Hoàn Thành (TP/SL Hit)'),
        ('CANCELLED', 'Đã Hủy (Hết Hiệu Lực / Phá Cấu Trúc)'),
    ]

    wallet = models.ForeignKey(WalletAccount, on_delete=models.CASCADE, related_name='trading_plans', verbose_name="Ví Áp Dụng")
    symbol = models.CharField(max_length=30, verbose_name="Mã Cặp")
    timeframe = models.CharField(max_length=10, default='M15', verbose_name="Khung Thời Gian")
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, verbose_name="Hướng Lệnh")
    
    # Entry Zone & Protection Levels
    entry_price = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Giá Kích Hoạt Entry")
    entry_zone_low = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Vùng Entry Min")
    entry_zone_high = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Vùng Entry Max")
    
    stop_loss = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Cắt Lỗ (Stop Loss)")
    take_profit_1 = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Chốt Lời 1 (TP1)")
    take_profit_2 = models.DecimalField(max_digits=15, decimal_places=5, verbose_name="Chốt Lời 2 (TP2)")
    
    rr_ratio = models.FloatField(default=2.0, verbose_name="Tỷ Lệ Risk:Reward (R:R)")
    calculated_lot = models.FloatField(default=0.1, verbose_name="Khối Lượng Tính Toán (Lot)")
    risk_amount_usd = models.DecimalField(max_digits=12, decimal_places=2, default=150.00, verbose_name="Rủi Ro Dự Tính ($)")
    
    rationale = models.TextField(verbose_name="Lý Do Phân Tích Vào Lệnh")
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='PENDING_TRIGGER', verbose_name="Trạng Thái Plan")
    
    triggered_at = models.DateTimeField(null=True, blank=True, verbose_name="Thời Điểm Kích Hoạt Lệnh")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Thời Điểm Tạo Plan")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Kế Hoạch Giao Dịch AI"
        verbose_name_plural = "Danh Sách Kế Hoạch Giao Dịch"
        ordering = ['-created_at']

    def __str__(self):
        return f"Plan #{self.id} [{self.wallet.name}] {self.symbol} {self.direction} @ {self.entry_price} ({self.status})"

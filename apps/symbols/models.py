from django.db import models
from django.utils import timezone

class SymbolConfig(models.Model):
    CATEGORIES = [
        ('METALS', 'Kim Loại Quý (Gold/Silver)'),
        ('FOREX', 'Ngoại Hối (Forex Major/Minor)'),
        ('CRYPTO', 'Tiền Mã Hóa (Crypto)'),
        ('COMMODITIES', 'Hàng Hóa (Dầu/Khí)'),
        ('INDICES', 'Chỉ Số Chứng Khoán'),
    ]

    TIMEFRAMES = [
        ('M1', '1 Phút (M1)'),
        ('M5', '5 Phút (M5)'),
        ('M15', '15 Phút (M15)'),
        ('M30', '30 Phút (M30)'),
        ('H1', '1 Giờ (H1)'),
        ('H4', '4 Giờ (H4)'),
        ('D1', '1 Ngày (D1)'),
    ]

    STRATEGIES = [
        ('SMC_TREND', 'SMC & Trend Following (EMA + RSI + ATR)'),
        ('SCALPING_BB', 'Bollinger Bands & Dynamic Momentum Scalper'),
        ('BREAKOUT_SESSION', 'Breakout Đỉnh Đáy Phiên Á/Âu & S/R Zones'),
    ]

    symbol = models.CharField(max_length=30, unique=True, verbose_name="Mã Cặp (Symbol)")
    display_name = models.CharField(max_length=100, verbose_name="Tên Hiển Thị")
    category = models.CharField(max_length=30, choices=CATEGORIES, default='METALS', verbose_name="Phân Loại")
    
    digits = models.IntegerField(default=2, verbose_name="Số Chữ Số Thập Phân (Digits)")
    point_size = models.FloatField(default=0.01, verbose_name="Kích Thước Point")
    contract_size = models.FloatField(default=100.0, verbose_name="Contract Size (Kích thước hợp đồng)")
    
    timeframe = models.CharField(max_length=10, choices=TIMEFRAMES, default='M15', verbose_name="Khung Thời Gian Mặc Định")
    strategy = models.CharField(max_length=30, choices=STRATEGIES, default='SMC_TREND', verbose_name="Chiến Thuật Áp Dụng")
    
    # Real-time Market Data
    current_price = models.DecimalField(max_digits=15, decimal_places=5, default=2750.00, verbose_name="Giá Thị Trường Hiện Tại")
    current_bid = models.DecimalField(max_digits=15, decimal_places=5, default=2749.85, verbose_name="Giá Bid")
    current_ask = models.DecimalField(max_digits=15, decimal_places=5, default=2750.15, verbose_name="Giá Ask")
    current_spread_pips = models.FloatField(default=1.2, verbose_name="Spread Hiện Tại (Pips)")
    max_allowed_spread = models.FloatField(default=3.5, verbose_name="Spread Tối Đa Cho Phép (Pips)")
    atr_value = models.FloatField(default=12.5, verbose_name="ATR Biến Động")
    
    is_active = models.BooleanField(default=True, verbose_name="Bật Giao Dịch Tự Động")
    last_scanned_at = models.DateTimeField(default=timezone.now, verbose_name="Thời Điểm Quét Gần Nhất")
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def price_change_24h(self) -> float:
        """Tỷ lệ biến động giá 24h (mặc định 0.0 nếu chưa có lịch sử nến ngày)."""
        return 0.0

    class Meta:
        verbose_name = "Cặp Giao Dịch Exness"
        verbose_name_plural = "Danh Sách Cặp Giao Dịch"
        ordering = ['symbol']

    def __str__(self):
        return f"{self.symbol} - {self.display_name}"

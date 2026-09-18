from django.db import models
from django.utils import timezone
import json

class MarketForecast(models.Model):
    TREND_CHOICES = [
        ('BULLISH', 'Tăng Giá Mạnh (Bullish Momentum)'),
        ('BEARISH', 'Giảm Giá Mạnh (Bearish Momentum)'),
        ('SIDEWAY', 'Đi Ngang Tích Lũy (Sideway / Consolidation)'),
    ]

    ACTION_CHOICES = [
        ('READY_TO_BUY', 'Sẵn Sàng Vào Mua (Buy Setup Ready)'),
        ('READY_TO_SELL', 'Sẵn Sàng Vào Bán (Sell Setup Ready)'),
        ('WAIT_FOR_PULLBACK', 'Chờ Điểm Hồi Tối Ưu (Wait for Pullback)'),
        ('BREAKOUT_PENDING', 'Chờ Phá Vỡ Vùng Giá (Breakout Pending)'),
        ('MONITORING', 'Theo Dõi Cấu Trúc (Monitoring)'),
    ]

    SETUP_STATUS_CHOICES = [
        ('WAITING', 'Chưa Có Setup Rõ Ràng (Waiting)'),
        ('WATCHING_BUY', 'Thiên Hướng Mua, Chưa Đủ Kích Hoạt (Watching Buy)'),
        ('WATCHING_SELL', 'Thiên Hướng Bán, Chưa Đủ Kích Hoạt (Watching Sell)'),
        ('BUY_READY', 'Đủ Điều Kiện Mua (Buy Ready)'),
        ('SELL_READY', 'Đủ Điều Kiện Bán (Sell Ready)'),
    ]

    symbol = models.CharField(max_length=30, verbose_name="Mã Cặp")
    timeframe = models.CharField(max_length=10, default='M15', verbose_name="Khung Thời Gian Phân Tích")
    
    # Forward-Looking Market Predictions
    trend_bias = models.CharField(max_length=20, choices=TREND_CHOICES, default='BULLISH', verbose_name="Xu Hướng Dự Báo Kế Tiếp")
    confidence_score = models.FloatField(default=85.0, verbose_name="Độ Tin Cậy Dự Báo (%)")
    
    current_price = models.DecimalField(max_digits=15, decimal_places=5, default=2750.00, verbose_name="Giá Thị Trường")
    projected_target_zone = models.CharField(max_length=100, default="2762.00 - 2770.00", verbose_name="Vùng Giá Mục Tiêu Tiếp Theo")
    
    next_resistance_1 = models.DecimalField(max_digits=15, decimal_places=5, default=2760.00, verbose_name="Mức Kháng Cự 1 (Next R1)")
    next_resistance_2 = models.DecimalField(max_digits=15, decimal_places=5, default=2775.00, verbose_name="Mức Kháng Cự 2 (Next R2)")
    next_support_1 = models.DecimalField(max_digits=15, decimal_places=5, default=2740.00, verbose_name="Mức Hỗ Trợ 1 (Next S1)")
    next_support_2 = models.DecimalField(max_digits=15, decimal_places=5, default=2728.00, verbose_name="Mức Hỗ Trợ 2 (Next S2)")
    
    trigger_condition = models.TextField(verbose_name="Điều Kiện Kích Hoạt Lệnh Tiếp Theo")
    smc_structure = models.CharField(max_length=150, default="H1 Bullish BOS + M15 Order Block Retest", verbose_name="Cấu Trúc Thị Trường (SMC/Price Action)")
    
    # Indicator Snapshots in JSON format
    indicators_json = models.TextField(default='{}', verbose_name="Dữ Liệu Chỉ Báo Kỹ Thuật")
    analysis_rationale = models.TextField(verbose_name="Lý Do Phân Tích Chi Tiết Của Bot")
    recommended_action = models.CharField(max_length=30, choices=ACTION_CHOICES, default='WAIT_FOR_PULLBACK', verbose_name="Hành Động Khuyến Nghị")
    setup_status = models.CharField(max_length=20, choices=SETUP_STATUS_CHOICES, default='WAITING', verbose_name="Trạng Thái Setup Đa Khung")
    
    updated_at = models.DateTimeField(default=timezone.now, verbose_name="Cập Nhật Lúc")

    class Meta:
        verbose_name = "Dự Báo Thị Trường Bot"
        verbose_name_plural = "Dự Báo & Phân Tích Tiếp Theo"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.symbol} [{self.timeframe}] -> {self.trend_bias} ({self.confidence_score}%)"

    @property
    def indicators(self):
        try:
            return json.loads(self.indicators_json)
        except Exception:
            return {}

    def set_indicators(self, data_dict):
        self.indicators_json = json.dumps(data_dict)

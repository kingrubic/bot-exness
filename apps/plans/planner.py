from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.plans.models import TradingPlan

# Plan FAILED/CANCELLED xóa ngay. Plan chờ khớp quá lâu cũng xóa (lướt sóng ngắn hạn).
STALE_PLAN_SECONDS = 180

class AutoPlanGenerator:
    """
    Engine tự động sinh Kế Hoạch Giao Dịch (Trading Plan) cho từng Ví Exness
    dựa trên kết quả phân tích kỹ thuật và tham số quản trị rủi ro.
    """

    @staticmethod
    def generate_plan_for_wallet(wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> TradingPlan:
        # Check if symbol is allowed for this wallet
        if symbol_config.symbol not in wallet.allowed_symbols:
            return None

        # 1. HƯỚNG VÀO LỆNH THEO XU HƯỚNG THỊ TRƯỜNG (TREND-FOLLOWING)
        if forecast.trend_bias == 'BULLISH':
            direction = 'BUY'
        elif forecast.trend_bias == 'BEARISH':
            direction = 'SELL'
        else:
            if forecast.recommended_action == 'READY_TO_BUY' or 'BUY' in str(forecast.recommended_action):
                direction = 'BUY'
            elif forecast.recommended_action == 'READY_TO_SELL' or 'SELL' in str(forecast.recommended_action):
                direction = 'SELL'
            else:
                direction = 'BUY'

        try:
            symbol_config.refresh_from_db()
        except Exception:
            pass

        # Luôn khớp giá MARKET (Ask khi BUY, Bid khi SELL) tại thời điểm lập kế hoạch
        entry_price = AutoPlanGenerator.market_entry_price(symbol_config, direction)

        lot = float(wallet.default_lot_size or 0.01)
        lot = max(0.01, round(lot, 2))
        min_tp = float(wallet.min_take_profit_usd or 1.0)

        tag_name = "AI Lướt Sóng Ngắn Hạn (Scale-In)" if is_pyramiding else "AI Lướt Sóng Ngắn Hạn"
        max_n = int(wallet.max_open_trades or 1)
        rationale = (
            f"{tag_name}: Khớp MARKET {direction} {lot} Lot (Ask/Bid sàn) theo xu hướng {forecast.trend_bias}. "
            f"Không cắt lỗ. Chỉ chốt khi lãi ròng >= ${min_tp:.2f}. "
            f"Cho phép tối đa {max_n} lệnh mở đồng thời trên ví."
        )

        plan = TradingPlan.objects.create(
            wallet=wallet,
            symbol=symbol_config.symbol,
            timeframe=symbol_config.timeframe,
            direction=direction,
            entry_price=entry_price,
            entry_zone_low=entry_price,
            entry_zone_high=entry_price,
            stop_loss=None,
            take_profit_1=None,
            take_profit_2=None,
            rr_ratio=1.0,
            calculated_lot=lot,
            risk_amount_usd=Decimal('0.00'),
            rationale=rationale,
            status='PENDING_TRIGGER',
            created_at=timezone.now()
        )
        return plan

    @staticmethod
    def market_entry_price(symbol_config: SymbolConfig, direction: str) -> Decimal:
        """Giá vào lệnh MARKET: Ask khi BUY, Bid khi SELL."""
        digits = int(getattr(symbol_config, 'digits', 5) or 5)
        if direction == 'BUY':
            raw = symbol_config.current_ask or symbol_config.current_price
        else:
            raw = symbol_config.current_bid or symbol_config.current_price
        if raw is None:
            return Decimal('0')
        return Decimal(str(round(float(raw), digits)))

    @classmethod
    def refresh_symbol_market_price(cls, symbol_name: str) -> SymbolConfig:
        """Lấy lại giá bid/ask mới nhất trước khi lập plan / khớp lệnh."""
        try:
            from apps.trading.live_market_feed import LiveMarketFeedService
            LiveMarketFeedService.sync_all_symbols()
        except Exception:
            pass
        return SymbolConfig.objects.filter(symbol=symbol_name).first()

    @classmethod
    def update_or_create_plan_for_wallet(cls, wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> TradingPlan:
        """
        Cập nhật kế hoạch giao dịch AI liên tục theo biến động giá thị trường thời gian thực.
        Nếu đã có plan PENDING_TRIGGER thì update giá entry, SL, TP, Lot mới nhất theo nến và giá sàn.
        Nếu chưa có thì tạo plan mới.
        """
        existing_plan = TradingPlan.objects.filter(
            wallet=wallet,
            symbol=symbol_config.symbol,
            status='PENDING_TRIGGER'
        ).first()

        try:
            symbol_config.refresh_from_db()
        except Exception:
            pass

        new_plan = cls.generate_plan_for_wallet(wallet, symbol_config, forecast, is_pyramiding=is_pyramiding)
        if not new_plan:
            return existing_plan

        if existing_plan and existing_plan.id != new_plan.id:
            # Sync new calculations into existing plan to keep stable ID
            existing_plan.direction = new_plan.direction
            existing_plan.entry_price = new_plan.entry_price
            existing_plan.entry_zone_low = new_plan.entry_zone_low
            existing_plan.entry_zone_high = new_plan.entry_zone_high
            existing_plan.stop_loss = new_plan.stop_loss
            existing_plan.take_profit_1 = new_plan.take_profit_1
            existing_plan.take_profit_2 = new_plan.take_profit_2
            existing_plan.rr_ratio = new_plan.rr_ratio
            existing_plan.calculated_lot = new_plan.calculated_lot
            existing_plan.risk_amount_usd = new_plan.risk_amount_usd
            existing_plan.rationale = new_plan.rationale
            existing_plan.save()
            new_plan.delete()
            return existing_plan
        return new_plan

    @classmethod
    def discard_plan(cls, plan: TradingPlan) -> None:
        """Bỏ plan thất bại / hết hiệu lực: xóa hẳn khỏi DB."""
        if not plan or not getattr(plan, 'pk', None):
            return
        try:
            plan.delete()
        except Exception:
            pass

    @classmethod
    def purge_dead_plans(cls) -> int:
        """
        Xóa plan FAILED/CANCELLED ngay, và plan chưa khớp (PENDING_TRIGGER / PENDING / ANALYZING)
        nếu đã quá STALE_PLAN_SECONDS. Không đụng EXECUTING / COMPLETED (đã gắn vị thế).
        """
        cutoff = timezone.now() - timedelta(seconds=STALE_PLAN_SECONDS)
        dead_qs = TradingPlan.objects.filter(status__in=['FAILED', 'CANCELLED'])
        stale_qs = TradingPlan.objects.filter(
            status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING'],
            created_at__lt=cutoff,
        )
        count = dead_qs.count() + stale_qs.count()
        if count:
            dead_qs.delete()
            stale_qs.delete()
        return count

    @classmethod
    def refresh_plans_for_wallet(cls, wallet: WalletAccount) -> list:
        """
        1. Xóa toàn bộ các Trading Plan AI chưa khớp lệnh cũ của ví (status in PENDING_TRIGGER, ANALYZING, CANCELLED).
        2. Tính toán và sinh lại các Trading Plan mới dựa trên số dư hiện tại và danh sách các cặp cho phép mới.
        """
        # 1. Xóa các Plan cũ chưa kích hoạt thành lệnh
        TradingPlan.objects.filter(
            wallet=wallet,
            status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING', 'CANCELLED', 'FAILED']
        ).delete()

        # 2. Sinh các Plan mới cho từng cặp trong danh sách allowed_symbols
        created_plans = []
        from apps.analysis.analyzer import TechnicalAnalyzer

        for sym_name in wallet.allowed_symbols:
            sym_config = SymbolConfig.objects.filter(symbol=sym_name).first()
            if not sym_config:
                continue

            forecast = MarketForecast.objects.filter(symbol=sym_name).first()
            if not forecast:
                forecast = TechnicalAnalyzer.generate_market_analysis(sym_config)

            if forecast:
                try:
                    plan = cls.generate_plan_for_wallet(wallet, sym_config, forecast)
                    if plan:
                        created_plans.append(plan)
                except Exception:
                    pass

        return created_plans


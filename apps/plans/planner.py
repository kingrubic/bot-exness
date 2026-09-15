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
        fields = AutoPlanGenerator._plan_fields(wallet, symbol_config, forecast, is_pyramiding=is_pyramiding)
        if not fields:
            return None
        fields['created_at'] = timezone.now()
        return TradingPlan.objects.create(**fields)

    @staticmethod
    def _plan_fields(wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast, is_pyramiding: bool = False) -> dict:

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
            f"Chốt lời khi lãi ròng >= ${min_tp:.2f}. Lệnh lỗ giữ nguyên (gồng) đến khi về lãi. "
            f"Max daily loss {float(wallet.max_daily_loss_percent or 0):.2f}%/ngày chỉ chặn mở thêm lệnh. "
            f"Cho phép tối đa {max_n} lệnh mở đồng thời trên ví."
        )

        fields = {
            'wallet': wallet,
            'symbol': symbol_config.symbol,
            'timeframe': symbol_config.timeframe,
            'direction': direction,
            'entry_price': entry_price,
            'entry_zone_low': entry_price,
            'entry_zone_high': entry_price,
            'stop_loss': None,
            'take_profit_1': None,
            'take_profit_2': None,
            'rr_ratio': 1.0,
            'calculated_lot': lot,
            'risk_amount_usd': Decimal('0.00'),
            'rationale': rationale,
            'status': 'PENDING_TRIGGER',
        }
        return fields

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
        """Làm mới 1 plan hiện tại cho ví+cặp: plan cũ (không EXECUTING) bị xóa, không tích dồn."""
        if symbol_config.symbol not in wallet.allowed_symbols:
            return None
        try:
            symbol_config.refresh_from_db()
        except Exception:
            pass

        fields = cls._plan_fields(wallet, symbol_config, forecast, is_pyramiding=is_pyramiding)
        if not fields:
            return None

        # Bỏ plan chết + các plan chờ cũ cùng ví/cặp; giữ EXECUTING (đang có lệnh mở)
        TradingPlan.objects.filter(
            wallet=wallet,
            symbol=symbol_config.symbol,
            status__in=['FAILED', 'CANCELLED', 'COMPLETED', 'PENDING', 'ANALYZING'],
        ).delete()

        pending_qs = TradingPlan.objects.filter(
            wallet=wallet,
            symbol=symbol_config.symbol,
            status='PENDING_TRIGGER',
        ).order_by('-updated_at', '-created_at')
        keep = pending_qs.first()
        if keep:
            pending_qs.exclude(pk=keep.pk).delete()
            for k, v in fields.items():
                setattr(keep, k, v)
            keep.status = 'PENDING_TRIGGER'
            keep.save()
            return keep

        fields['created_at'] = timezone.now()
        return TradingPlan.objects.create(**fields)

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
        Không tích dồn: xóa FAILED/CANCELLED/COMPLETED ngay.
        Chỉ giữ 1 plan PENDING mới nhất mỗi ví+cặp, và plan EXECUTING (lệnh đang mở).
        """
        dead_qs = TradingPlan.objects.filter(status__in=['FAILED', 'CANCELLED', 'COMPLETED'])
        count = dead_qs.count()
        dead_qs.delete()

        cutoff = timezone.now() - timedelta(seconds=STALE_PLAN_SECONDS)
        stale_qs = TradingPlan.objects.filter(
            status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING'],
            created_at__lt=cutoff,
        )
        count += stale_qs.count()
        stale_qs.delete()

        pending = list(
            TradingPlan.objects.filter(
                status__in=['PENDING_TRIGGER', 'PENDING', 'ANALYZING']
            ).order_by('wallet_id', 'symbol', '-updated_at', '-created_at')
        )
        seen = set()
        drop_ids = []
        for p in pending:
            key = (p.wallet_id, p.symbol)
            if key in seen:
                drop_ids.append(p.pk)
            else:
                seen.add(key)
        if drop_ids:
            count += len(drop_ids)
            TradingPlan.objects.filter(pk__in=drop_ids).delete()
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


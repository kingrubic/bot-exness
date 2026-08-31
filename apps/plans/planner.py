from decimal import Decimal
import random
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.plans.models import TradingPlan

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

        price = float(symbol_config.current_price)
        digits = symbol_config.digits
        
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
                direction = 'BUY' if price > float(symbol_config.current_price or price) else 'SELL'

        # Khớp thẳng tại giá thị trường hiện tại
        if direction == 'BUY':
            entry_price = float(symbol_config.current_ask or symbol_config.current_price)
        else:
            entry_price = float(symbol_config.current_bid or symbol_config.current_price)
        entry_price = round(entry_price, digits)

        lot = float(wallet.default_lot_size or 0.01)
        lot = max(0.01, round(lot, 2))
        eq_val = float(wallet.equity if (wallet.equity and wallet.equity > 0) else (wallet.balance or 0.0))

        tag_name = "AI Nhồi Lệnh Theo Trend (Scale-In)" if is_pyramiding else "AI Đánh Lướt Sóng Theo Trend (Micro-Scalping)"
        rationale = (
            f"{tag_name}: Khớp thị trường {direction} {lot} Lot theo xu hướng {forecast.trend_bias}. "
            f"Kiểm soát hạn mức an toàn vốn (${eq_val:.2f}), đánh lướt sóng nhanh và tự động chốt lời ngay khi có lãi ròng sau phí."
        )

        plan = TradingPlan.objects.create(
            wallet=wallet,
            symbol=symbol_config.symbol,
            timeframe=symbol_config.timeframe,
            direction=direction,
            entry_price=Decimal(str(entry_price)),
            entry_zone_low=Decimal(str(entry_price)),
            entry_zone_high=Decimal(str(entry_price)),
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
            existing_plan.created_at = timezone.now()
            existing_plan.save()
            new_plan.delete()
            return existing_plan
        return new_plan

    @classmethod
    def refresh_plans_for_wallet(cls, wallet: WalletAccount) -> list:
        """
        1. Xóa toàn bộ các Trading Plan AI chưa khớp lệnh cũ của ví (status in PENDING_TRIGGER, ANALYZING, CANCELLED).
        2. Tính toán và sinh lại các Trading Plan mới dựa trên số dư hiện tại và danh sách các cặp cho phép mới.
        """
        # 1. Xóa các Plan cũ chưa kích hoạt thành lệnh
        TradingPlan.objects.filter(
            wallet=wallet,
            status__in=['PENDING_TRIGGER', 'ANALYZING', 'CANCELLED']
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


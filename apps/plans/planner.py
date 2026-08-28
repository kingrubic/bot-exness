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
    def generate_plan_for_wallet(wallet: WalletAccount, symbol_config: SymbolConfig, forecast: MarketForecast) -> TradingPlan:
        # Check if symbol is allowed for this wallet
        if symbol_config.symbol not in wallet.allowed_symbols:
            return None

        # If market is sideway with low confidence, do not generate risky plan
        if forecast.trend_bias == 'SIDEWAY' and forecast.confidence_score < 75.0:
            return None

        price = float(symbol_config.current_price)
        digits = symbol_config.digits
        atr = max(float(symbol_config.atr_value), price * 0.005, 5 * (10 ** (-digits)))
        
        direction = 'BUY' if forecast.trend_bias == 'BULLISH' else 'SELL'
        
        # Enforce healthy SL distance (at least 1.0 * ATR)
        sl_diff = round(atr * random.uniform(1.0, 1.4), digits)
        min_diff = max(5 * (10 ** (-digits)), price * 0.001)
        sl_diff = max(sl_diff, min_diff)
        
        tp1_diff = round(sl_diff * random.uniform(1.6, 2.2), digits)
        tp2_diff = round(sl_diff * random.uniform(2.6, 3.6), digits)

        if direction == 'BUY':
            entry_price = round(price * (1 - random.uniform(0.0005, 0.0015)), digits)
            entry_low = round(entry_price - (sl_diff * 0.2), digits)
            entry_high = round(entry_price + (sl_diff * 0.2), digits)
            sl = round(entry_price - sl_diff, digits)
            tp1 = round(entry_price + tp1_diff, digits)
            tp2 = round(entry_price + tp2_diff, digits)
        else: # SELL
            entry_price = round(price * (1 + random.uniform(0.0005, 0.0015)), digits)
            entry_low = round(entry_price - (sl_diff * 0.2), digits)
            entry_high = round(entry_price + (sl_diff * 0.2), digits)
            sl = round(entry_price + sl_diff, digits)
            tp1 = round(entry_price - tp1_diff, digits)
            tp2 = round(entry_price - tp2_diff, digits)

        sl_distance = max(abs(entry_price - sl), min_diff)
        tp_distance = max(abs(tp1 - entry_price), sl_distance * 1.5)

        rr_ratio = round(tp_distance / max(sl_distance, 0.0001), 2)
        if rr_ratio < 1.4:
            rr_ratio = 1.6
        elif rr_ratio > 4.0:
            rr_ratio = 2.5

        # Calculate dynamic Lot Size based on wallet risk %
        balance = float(wallet.balance)
        risk_pct = wallet.risk_percent / 100.0
        risk_amount_usd = round(balance * risk_pct, 2)
        
        # Accurate lot calculation by category & contract size
        cat = getattr(symbol_config, 'category', 'FOREX')
        contract = float(symbol_config.contract_size or 100.0)
        
        if cat == 'CRYPTO':
            # 1 Lot = 1 coin. Loss in USD = lot * sl_distance * contract
            lot = risk_amount_usd / max(sl_distance * contract, 1.0)
        elif cat == 'METALS':
            lot = risk_amount_usd / max(sl_distance * contract, 1.0)
        elif cat == 'INDICES':
            lot = risk_amount_usd / max(sl_distance * contract, 1.0)
        else: # FOREX
            if "JPY" in symbol_config.symbol:
                lot = risk_amount_usd / max((sl_distance / max(price, 1.0)) * contract, 1.0)
            else:
                lot = risk_amount_usd / max(sl_distance * contract, 1.0)

        # Normalize lot size between 0.01 and max limit
        lot = max(0.01, min(round(lot, 2), 5.00))

        rationale = (
            f"Chiến thuật {symbol_config.get_strategy_display()}: "
            f"Dự báo {forecast.get_trend_bias_display()} với độ tin cậy {forecast.confidence_score}%. "
            f"Vùng Entry: {entry_low} - {entry_high}. Cắt lỗ bảo toàn vốn tại {sl}. "
            f"Tỷ lệ R:R = 1:{rr_ratio}. Khối lượng tính toán an toàn: {lot} Lot ({wallet.risk_percent}% vốn)."
        )

        plan = TradingPlan.objects.create(
            wallet=wallet,
            symbol=symbol_config.symbol,
            timeframe=symbol_config.timeframe,
            direction=direction,
            entry_price=Decimal(str(entry_price)),
            entry_zone_low=Decimal(str(entry_low)),
            entry_zone_high=Decimal(str(entry_high)),
            stop_loss=Decimal(str(sl)),
            take_profit_1=Decimal(str(tp1)),
            take_profit_2=Decimal(str(tp2)),
            rr_ratio=rr_ratio,
            calculated_lot=lot,
            risk_amount_usd=Decimal(str(risk_amount_usd)),
            rationale=rationale,
            status='PENDING_TRIGGER',
            created_at=timezone.now()
        )
        return plan

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


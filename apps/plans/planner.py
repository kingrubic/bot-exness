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
        atr = float(symbol_config.atr_value)
        digits = symbol_config.digits
        
        direction = 'BUY' if forecast.trend_bias == 'BULLISH' else 'SELL'
        
        if direction == 'BUY':
            entry_price = round(price * (1 - random.uniform(0.0005, 0.002)), digits)
            entry_low = round(entry_price - (atr * 0.2), digits)
            entry_high = round(entry_price + (atr * 0.2), digits)
            sl = round(entry_price - (atr * 1.2), digits)
            tp1 = round(entry_price + (atr * 1.8), digits)
            tp2 = round(entry_price + (atr * 3.0), digits)
            sl_distance = abs(entry_price - sl)
            tp_distance = abs(tp1 - entry_price)
        else: # SELL
            entry_price = round(price * (1 + random.uniform(0.0005, 0.002)), digits)
            entry_low = round(entry_price - (atr * 0.2), digits)
            entry_high = round(entry_price + (atr * 0.2), digits)
            sl = round(entry_price + (atr * 1.2), digits)
            tp1 = round(entry_price - (atr * 1.8), digits)
            tp2 = round(entry_price - (atr * 3.0), digits)
            sl_distance = abs(entry_price - sl)
            tp_distance = abs(tp1 - entry_price)

        rr_ratio = round(tp_distance / max(sl_distance, 0.0001), 2)
        if rr_ratio < 1.4:
            rr_ratio = 1.6

        # Calculate dynamic Lot Size based on wallet risk %
        balance = float(wallet.balance)
        risk_pct = wallet.risk_percent / 100.0
        risk_amount_usd = round(balance * risk_pct, 2)
        
        # Approximate lot calculation
        if "XAU" in symbol_config.symbol:
            lot = risk_amount_usd / max(sl_distance * 100, 1.0)
        elif "BTC" in symbol_config.symbol:
            lot = risk_amount_usd / max(sl_distance * 1, 1.0)
        else: # Forex
            lot = risk_amount_usd / max(sl_distance * 100000, 1.0)

        # Normalize lot size between 0.01 and 10.0
        lot = max(0.01, min(round(lot, 2), 5.00))

        rationale = (
            f"Chiến thuật {symbol_config.get_strategy_display()}: "
            f"Dự báo {forecast.get_trend_bias_display()} với độ tin cậy {forecast.confidence_score}%. "
            f"Vùng Entry: {entry_low} - {entry_high}. Cắt lỗ bảo toàn vốn dưới {sl}. "
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

"""Read-only entry validation shared by the planner, execution and dashboard.

USD limits use the existing linear USD-quoted contract model. The R multiple
is gross, before commission/swap/slippage; it is not a win probability.
"""
import math
from django.utils import timezone

MIN_REWARD_RISK = 1.5
MAX_SIGNAL_AGE_SECONDS = 30
MAX_ENTRY_DRIFT_ATR = 0.5


def entry_preview(wallet, symbol, forecast):
    def blocked(reason):
        return {'allowed': False, 'reason': reason}

    if wallet is None or symbol is None or forecast is None:
        return blocked('Chọn ví và chờ dữ liệu phân tích mới.')
    if not wallet.is_active or not symbol.is_active or symbol.symbol not in wallet.allowed_symbols:
        return blocked('Ví hoặc cặp chưa được phép giao dịch.')
    direction = {'READY_TO_BUY': 'BUY', 'READY_TO_SELL': 'SELL'}.get(forecast.recommended_action)
    if direction is None:
        return blocked('Chưa đủ xác nhận kỹ thuật; không vào lệnh.')
    age = (timezone.now() - forecast.updated_at).total_seconds()
    if not 0 <= age <= MAX_SIGNAL_AGE_SECONDS:
        return blocked('Tín hiệu quá hạn; chờ phân tích mới.')
    ind = forecast.indicators
    if not isinstance(ind, dict) or ind.get('signal_version') != 2 or not ind.get('data_ok'):
        return blocked('Chờ tín hiệu mới từ nến đã đóng và đủ dữ liệu.')
    from apps.analysis.analyzer import TIMEFRAME_SECONDS
    duration = TIMEFRAME_SECONDS.get(str(forecast.timeframe).upper(), 0)
    try:
        closed_age = timezone.now().timestamp() - float(ind['closed_candle_time']) - duration
        if not duration or not 0 <= closed_age <= duration + 60:
            return blocked('Nến tín hiệu chưa đóng hoặc đã cũ.')
        bid, ask = float(symbol.current_bid), float(symbol.current_ask)
        atr, signal, ema9 = (float(ind[k]) for k in ('atr', 'signal_price', 'ema9'))
        tp_usd, sl_usd = float(wallet.min_take_profit_usd or 0), float(wallet.max_stop_loss_usd or 0)
        lot, contract = float(wallet.default_lot_size), float(symbol.contract_size)
        risk_pct = float(wallet.risk_percent)
        equity = float(wallet.equity_db)
        # A zero equity on a never-synced local simulator uses its balance.
        if not wallet.mt5_login and equity == 0:
            equity = float(wallet.balance_db) + float(wallet.floating_pnl)
        daily_pct = float(wallet.max_daily_loss_percent)
        today_pnl = float(wallet.get_today_pnl() if getattr(wallet, 'pk', None) else wallet.today_pnl)
        max_spread, point = float(symbol.max_allowed_spread), float(symbol.point_size)
        numbers = (bid, ask, atr, signal, ema9, tp_usd, sl_usd, lot, contract,
                   risk_pct, equity, daily_pct, today_pnl, max_spread, point)
        if not all(math.isfinite(v) for v in numbers):
            return blocked('Dữ liệu giá hoặc cấu hình rủi ro không hợp lệ.')
    except (KeyError, TypeError, ValueError, OverflowError):
        return blocked('Thiếu dữ liệu giá hoặc cấu hình rủi ro.')
    if min(bid, ask, atr, signal, lot, contract, point, max_spread) <= 0 or ask < bid:
        return blocked('Giá Bid/Ask, ATR, lot hoặc thông số hợp đồng không hợp lệ.')
    if str(getattr(wallet, 'currency', '')).upper() != 'USD':
        return blocked('Chưa hỗ trợ quy đổi rủi ro cho tài khoản không dùng USD.')
    if not str(symbol.symbol).upper().endswith('USD'):
        return blocked('Chưa có quy đổi tiền tệ để tính SL USD chính xác cho cặp này.')
    if tp_usd <= 0 or sl_usd <= 0:
        return blocked('Cần đặt cả TP USD và SL USD dương trước khi mở lệnh mới.')
    if equity <= 0 or not 0 < risk_pct <= 100 or not 0 < daily_pct <= 100:
        return blocked('Equity hoặc giới hạn rủi ro của ví không hợp lệ.')
    if sl_usd > equity * risk_pct / 100:
        return blocked('SL USD vượt giới hạn rủi ro mỗi lệnh của ví.')
    open_risk = 0.0
    if getattr(wallet, 'pk', None):
        from apps.symbols.models import SymbolConfig
        for position in wallet.positions.all():
            spec = SymbolConfig.objects.filter(symbol=position.symbol).first()
            if not spec or not position.stop_loss or not position.symbol.upper().endswith('USD'):
                return blocked('Có vị thế chưa xác định được rủi ro SL; không mở thêm.')
            sign = 1 if position.position_type == 'BUY' else -1
            exposure = sign * (float(position.open_price) - float(position.stop_loss))
            exposure *= float(spec.contract_size) * float(position.lot_size)
            if not math.isfinite(exposure):
                return blocked('Rủi ro vị thế đang mở không hợp lệ.')
            open_risk += max(0, exposure)
    if max(0, -today_pnl) + open_risk + sl_usd > equity * daily_pct / 100:
        return blocked('Lệnh mới vượt ngân sách lỗ còn lại trong ngày.')
    pip = point * (10 if symbol.digits in (3, 5) else 1)
    if (ask - bid) / pip > max_spread + 1e-9 or ask - bid > atr * 0.15:
        return blocked('Spread vượt giới hạn cho phép.')
    entry = ask if direction == 'BUY' else bid
    if abs(entry - signal) > MAX_ENTRY_DRIFT_ATR * atr:
        return blocked('Giá đã lệch quá 0.5 ATR so với nến tín hiệu; chờ xác nhận mới.')
    if (direction == 'BUY' and bid < ema9) or (direction == 'SELL' and ask > ema9):
        return blocked('Giá hiện tại không còn xác nhận hướng EMA9.')
    sign = 1 if direction == 'BUY' else -1
    digits = int(symbol.digits)
    stop = round(entry - sign * sl_usd / (contract * lot), digits)
    target = round(entry + sign * tp_usd / (contract * lot), digits)
    loss_dist, reward_dist = sign * (entry - stop), sign * (target - entry)
    if min(stop, target, loss_dist, reward_dist) <= 0:
        return blocked('SL/TP sau làm tròn không nằm đúng phía của giá vào.')
    if loss_dist < max(atr * 0.5, ask - bid):
        return blocked('SL quá gần so với ATR/spread; cần điều chỉnh lot hoặc giới hạn USD.')
    risk_usd = loss_dist * contract * lot
    if risk_usd > sl_usd + 1e-7:
        return blocked('SL sau làm tròn vượt mức lỗ USD cho phép.')
    rr = reward_dist / loss_dist
    if rr + 1e-9 < MIN_REWARD_RISK:
        return blocked(f'Lợi nhuận/rủi ro trước phí {rr:.2f}R < {MIN_REWARD_RISK:.1f}R.')
    try:
        level = float(ind['r1' if direction == 'BUY' else 's1'])
        if not math.isfinite(level) or sign * (level - target) < atr * 0.1:
            return blocked('TP nằm quá sát hoặc vượt vùng cản; chưa đủ khoảng trống cho mục tiêu.')
    except (KeyError, TypeError, ValueError):
        return blocked('Thiếu vùng hỗ trợ/kháng cự để kiểm tra mục tiêu.')
    return {
        'allowed': True, 'reason': 'Đạt kiểm tra giá và rủi ro; còn kiểm tra trạng thái ví/MT5 và số lệnh.',
        'direction': direction, 'entry_price': entry, 'stop_loss': stop,
        'target_price': target, 'target_profit_usd': tp_usd,
        'rr_ratio': round(rr, 4), 'risk_amount_usd': round(risk_usd, 2),
        'calculated_lot': lot,
    }

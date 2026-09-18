"""Read-only entry validation shared by planner, execution and dashboard.

SL/TP ban đầu lấy từ kế hoạch cấu trúc đa khung. Giới hạn USD là tùy chọn:
max_stop_loss_usd, nếu có, là trần rủi ro chứ không phải nguồn tạo SL.
R multiple là gross trước commission/swap/slippage, không phải xác suất thắng.
"""
import math
from django.utils import timezone

from apps.analysis.config import MIN_RISK_REWARD

# Ngưỡng RR dùng chung với module phân tích — chỉnh trong apps/analysis/config.py.
MIN_REWARD_RISK = MIN_RISK_REWARD
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
        tp_usd = float(wallet.min_take_profit_usd or 0)
        sl_usd = float(wallet.max_stop_loss_usd or 0)
        structural_stop = float(ind['stop_loss'])
        structural_tp1 = float(ind['take_profit_1'])
        structural_tp2 = float(ind['take_profit_2'])
        lot, contract = float(wallet.default_lot_size), float(symbol.contract_size)
        risk_pct = float(wallet.risk_percent)
        equity = float(wallet.equity_db)
        # A zero equity on a never-synced local simulator uses its balance.
        if not wallet.mt5_login and equity == 0:
            equity = float(wallet.balance_db) + float(wallet.floating_pnl)
        daily_pct = float(wallet.max_daily_loss_percent)
        today_pnl = float(wallet.get_today_pnl() if getattr(wallet, 'pk', None) else wallet.today_pnl)
        max_spread, point = float(symbol.max_allowed_spread), float(symbol.point_size)
        numbers = (
            bid, ask, atr, signal, ema9, tp_usd, sl_usd,
            structural_stop, structural_tp1, structural_tp2,
            lot, contract, risk_pct, equity, daily_pct, today_pnl,
            max_spread, point,
        )
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
    if equity <= 0 or not 0 < risk_pct <= 100 or not 0 < daily_pct <= 100:
        return blocked('Equity hoặc giới hạn rủi ro của ví không hợp lệ.')
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
    stop = round(structural_stop, digits)
    tp1 = round(structural_tp1, digits)
    tp2 = round(structural_tp2, digits)
    # MT5 chỉ nhận một TP cho toàn vị thế. Dùng TP2 vì đây là mục tiêu đã vượt
    # kiểm tra RR; TP1 vẫn lưu trên plan để hiển thị/chốt một phần sau này.
    target = tp2 if sign * (tp2 - entry) > 0 else tp1
    loss_dist, reward_dist = sign * (entry - stop), sign * (target - entry)
    if min(stop, target, loss_dist, reward_dist) <= 0:
        return blocked('SL/TP sau làm tròn không nằm đúng phía của giá vào.')
    if loss_dist < max(atr * 0.5, ask - bid):
        return blocked('SL quá gần so với ATR/spread; cần điều chỉnh lot hoặc giới hạn USD.')
    risk_usd = loss_dist * contract * lot
    if risk_usd > equity * risk_pct / 100 + 1e-7:
        return blocked('SL cấu trúc vượt giới hạn rủi ro mỗi lệnh của ví.')
    if sl_usd > 0 and risk_usd > sl_usd + 1e-7:
        return blocked(
            f'Rủi ro SL cấu trúc ${risk_usd:.2f} vượt Max Cắt Lỗ ${sl_usd:.2f}.'
        )
    if max(0, -today_pnl) + open_risk + risk_usd > equity * daily_pct / 100:
        return blocked('Lệnh mới vượt ngân sách lỗ còn lại trong ngày.')
    rr = reward_dist / loss_dist
    if rr + 1e-9 < MIN_REWARD_RISK:
        return blocked(f'Lợi nhuận/rủi ro trước phí {rr:.2f}R < {MIN_REWARD_RISK:.1f}R.')
    target_profit_usd = reward_dist * contract * lot
    return {
        'allowed': True,
        'reason': (
            'Đạt kiểm tra SL/TP cấu trúc và rủi ro; '
            'còn kiểm tra trạng thái ví/MT5 và số lệnh.'
        ),
        'direction': direction, 'entry_price': entry, 'stop_loss': stop,
        'take_profit_1': tp1, 'take_profit_2': tp2,
        'target_price': target,
        'target_profit_usd': round(target_profit_usd, 2),
        'stop_source': 'STRUCTURE_ATR',
        'target_source': 'STRUCTURE_TP2' if target == tp2 else 'STRUCTURE_TP1',
        'rr_ratio': round(rr, 4), 'risk_amount_usd': round(risk_usd, 2),
        'calculated_lot': lot,
    }

import json
import logging
import math
import time
from decimal import Decimal
from django.utils import timezone
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast

logger = logging.getLogger(__name__)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _ema(values: list[float], period: int) -> list[float | None]:
    """EMA series; None until warm-up complete."""
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period or period < 1:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 1e-12:
        return 50.0 if avg_gain <= 1e-12 else 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _macd_hist(closes: list[float], fast=12, slow=26, signal=9) -> float | None:
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = []
    for a, b in zip(ema_fast, ema_slow):
        if a is None or b is None:
            macd_line.append(None)
        else:
            macd_line.append(a - b)
    # signal on non-None macd values — build compact series then map back
    compact = [v for v in macd_line if v is not None]
    if len(compact) < signal:
        return None
    sig = _ema(compact, signal)
    if not sig or sig[-1] is None:
        return None
    return round(compact[-1] - sig[-1], 5)


def _bollinger(closes: list[float], period: int = 20, mult: float = 2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = var ** 0.5
    return mid, mid + mult * std, mid - mult * std


def _swing_levels(highs: list[float], lows: list[float], lookback: int = 20):
    h = highs[-lookback:] if len(highs) >= lookback else highs
    l = lows[-lookback:] if len(lows) >= lookback else lows
    if not h or not l:
        return None, None, None, None
    r1 = max(h)
    s1 = min(l)
    # second extremes excluding the first
    h2 = [x for x in h if x < r1 - 1e-12] or h
    l2 = [x for x in l if x > s1 + 1e-12] or l
    r2 = max(h2)
    s2 = min(l2)
    return r1, r2, s1, s2


def _near_level(price: float, level: float | None, atr: float, frac: float = 0.4) -> bool:
    """Giá đang sát mức S/R (trong khoảng frac × ATR)."""
    if level is None or atr is None or atr <= 0:
        return False
    return abs(price - level) <= max(atr * frac, 1e-9)


def _recent_structure_bias(highs: list[float], lows: list[float], closes: list[float], lookback: int = 24) -> str:
    """
    Xu hướng cấu trúc gần nhất. Ưu tiên impulse vài nến cuối (dump/rally mạnh)
    để không bị sóng tăng cũ che mất cú giảm đang chạy.
    """
    if len(closes) < max(8, lookback // 2):
        return 'SIDEWAY'
    n = min(lookback, len(closes), len(highs), len(lows))
    h = highs[-n:]
    l = lows[-n:]
    c = closes[-n:]
    rng = max(max(h) - min(l), 1e-9)

    # Impulse 3–6 nến gần nhất — bắt dump/rally đang chạy trên chart
    for win in (3, 4, 5, 6):
        if len(c) < win + 1:
            continue
        move = c[-1] - c[-win]
        if move <= -0.55 * rng * (win / 6.0) or move <= -0.8 * (rng / max(n / 5.0, 1.0)):
            return 'BEARISH'
        if move >= 0.55 * rng * (win / 6.0) or move >= 0.8 * (rng / max(n / 5.0, 1.0)):
            return 'BULLISH'

    # Đếm nến đỏ/xanh liên tiếp gần nhất
    red_streak = 0
    for i in range(len(c) - 1, max(len(c) - 6, 0) - 1, -1):
        if c[i] < c[i - 1]:
            red_streak += 1
        else:
            break
    green_streak = 0
    for i in range(len(c) - 1, max(len(c) - 6, 0) - 1, -1):
        if c[i] > c[i - 1]:
            green_streak += 1
        else:
            break
    if red_streak >= 3 and (c[-1] - c[-1 - red_streak]) < -0.25 * rng:
        return 'BEARISH'
    if green_streak >= 3 and (c[-1] - c[-1 - green_streak]) > 0.25 * rng:
        return 'BULLISH'

    mid = n // 2
    if mid < 3:
        return 'SIDEWAY'
    hh_prev, hh_now = max(h[:mid]), max(h[mid:])
    ll_prev, ll_now = min(l[:mid]), min(l[mid:])
    slope = c[-1] - c[0]
    atr_proxy = max(rng / max(n / 4.0, 1.0), 1e-9)

    bear_struct = hh_now < hh_prev and ll_now <= ll_prev
    bull_struct = hh_now > hh_prev and ll_now >= ll_prev
    bear_slope = slope < -0.35 * atr_proxy
    bull_slope = slope > 0.35 * atr_proxy

    if bear_struct or (bear_slope and ll_now <= ll_prev):
        return 'BEARISH'
    if bull_struct or (bull_slope and hh_now >= hh_prev):
        return 'BULLISH'
    if bear_slope:
        return 'BEARISH'
    if bull_slope:
        return 'BULLISH'
    return 'SIDEWAY'


def _sr_entry_gate(price: float, atr: float, r1, r2, s1, s2, want: str) -> tuple[bool, str]:
    """
    Cổng S/R: không BUY sát kháng cự, không SELL sát hỗ trợ.
    Ưu tiên BUY gần support / SELL gần resistance (theo xu hướng).
    Trả (ok, reason).
    """
    if atr is None or atr <= 0:
        return True, ''
    near_r = _near_level(price, r1, atr, 0.45) or _near_level(price, r2, atr, 0.35)
    near_s = _near_level(price, s1, atr, 0.45) or _near_level(price, s2, atr, 0.35)
    dist_r = abs(price - r1) if r1 is not None else None
    dist_s = abs(price - s1) if s1 is not None else None

    if want == 'BUY':
        if near_r:
            return False, f'Giá sát kháng cự R≈{r1} — không BUY đuổi đỉnh.'
        # Quá gần R hơn S → rủi ro đảo chiều
        if dist_r is not None and dist_s is not None and dist_r < dist_s * 0.55:
            return False, f'Gần kháng cự hơn hỗ trợ (R1={r1}, S1={s1}) — chờ hồi về S.'
        if near_s:
            return True, f'Giá gần hỗ trợ S≈{s1} — BUY theo vùng hỗ trợ.'
        return True, ''
    if want == 'SELL':
        if near_s:
            return False, f'Giá sát hỗ trợ S≈{s1} — không SELL đuổi đáy.'
        if dist_r is not None and dist_s is not None and dist_s < dist_r * 0.55:
            return False, f'Gần hỗ trợ hơn kháng cự (S1={s1}, R1={r1}) — chờ hồi về R.'
        if near_r:
            return True, f'Giá gần kháng cự R≈{r1} — SELL theo vùng kháng cự.'
        return True, ''
    return True, ''


TIMEFRAME_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800,
                     'H1': 3600, 'H4': 14400, 'D1': 86400}


def _closed_rates(rates, timeframe, now=None):
    """Accept only ordered, valid, completed candles with a recent final close."""
    duration = TIMEFRAME_SECONDS.get(str(timeframe).upper())
    if not duration:
        return []
    now = time.time() if now is None else now
    result = []
    previous = None
    for row in rates or []:
        try:
            stamp = float(row['time'])
            o, h, l, c = (float(row[k]) for k in ('open', 'high', 'low', 'close'))
            if not all(math.isfinite(x) for x in (stamp, o, h, l, c)):
                return []
            if stamp <= 0 or min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
                return []
            if previous is not None and stamp <= previous:
                return []
            previous = stamp
            if stamp + duration <= now:
                result.append(row)
        except (KeyError, TypeError, ValueError):
            return []
    if not result or now - float(result[-1]['time']) - duration > duration + 60:
        return []
    return result


def _extract_ohlc(rates) -> tuple[list[float], list[float], list[float], list[float]]:
    opens, highs, lows, closes = [], [], [], []
    for row in rates:
        try:
            o = _f(row['open'] if hasattr(row, '__getitem__') else getattr(row, 'open'))
            h = _f(row['high'] if hasattr(row, '__getitem__') else getattr(row, 'high'))
            l = _f(row['low'] if hasattr(row, '__getitem__') else getattr(row, 'low'))
            c = _f(row['close'] if hasattr(row, '__getitem__') else getattr(row, 'close'))
        except Exception:
            continue
        if c <= 0:
            continue
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
    return opens, highs, lows, closes


class TechnicalAnalyzer:
    """
    Phân tích thật từ nến MT5:
    - Lướt sóng: EMA9/21 + RSI + MACD + cấu trúc gần nhất + S/R — không BUY ngược downtrend / sát kháng cự.
    - Dài hạn: EMA50/200 + cấu trúc + S/R; trail SL theo ví.
    """

    @staticmethod
    def _is_scalp_mode(symbol_config: SymbolConfig) -> bool:
        tf = str(getattr(symbol_config, 'timeframe', '') or 'M15').upper()
        strat = str(getattr(symbol_config, 'strategy', '') or '').upper()
        if strat == 'SCALPING_BB' or tf in ('M1', 'M5'):
            return True
        return False

    @staticmethod
    def _load_rates(symbol: str, timeframe: str, count: int = 220):
        return _closed_rates(TechnicalAnalyzer._load_raw_rates(symbol, timeframe, count + 1), timeframe)

    @staticmethod
    def _load_raw_rates(symbol: str, timeframe: str, count: int = 220):
        """Lấy nến lịch sử đã có trên MT5 (native hoặc Wine). Không ngồi chờ nến mới đóng."""
        rates = None
        try:
            from apps.trading.mt5_session import MT5NativeSession
            rates = MT5NativeSession.copy_rates(symbol, timeframe or 'M15', count)
            if rates and len(rates) >= 30:
                return rates
        except Exception as e:
            logger.debug("copy_rates native %s %s: %s", symbol, timeframe, e)
        try:
            from apps.trading.mt5_connector import ExnessMT5Connector
            wine_rates = ExnessMT5Connector.copy_rates_from_bridge(symbol, timeframe or 'M15', count)
            if wine_rates and len(wine_rates) >= 30:
                return wine_rates
            if wine_rates and (not rates or len(wine_rates) > len(rates)):
                rates = wine_rates
        except Exception as e:
            logger.debug("copy_rates wine %s %s: %s", symbol, timeframe, e)
        if rates and len(rates) >= 30:
            return rates
        return None

    @staticmethod
    def _htf_bias(symbol: str, scalp_tf: str) -> str | None:
        """Xu hướng khung lớn + cấu trúc gần nhất. Siết hơn để chặn BUY trong downtrend."""
        tf = str(scalp_tf or 'M5').upper()
        htf = {'M1': 'M5', 'M5': 'M15', 'M15': 'H1'}.get(tf, 'H1')
        rates = TechnicalAnalyzer._load_rates(symbol, htf, 220)
        _o, highs, lows, closes = _extract_ohlc(rates or [])
        if len(closes) < 60:
            return None
        ema50_s = _ema(closes, 50)
        ema200_s = _ema(closes, 200)
        ema50 = ema50_s[-1] if ema50_s else None
        ema200 = ema200_s[-1] if ema200_s else None
        px = closes[-1]
        macd_h = _macd_hist(closes)
        struct = _recent_structure_bias(highs, lows, closes, lookback=30)
        if ema50 is None:
            return struct if struct != 'SIDEWAY' else None

        # Ưu tiên cấu trúc gần nhất khi rõ ràng
        if struct == 'BEARISH' and (px <= ema50 or (macd_h is not None and macd_h < 0)):
            return 'BEARISH'
        if struct == 'BULLISH' and (px >= ema50 or (macd_h is not None and macd_h > 0)):
            return 'BULLISH'

        if ema200 is not None:
            if ema50 > ema200 and px > ema50 and struct != 'BEARISH':
                return 'BULLISH'
            if ema50 < ema200 and px < ema50 and struct != 'BULLISH':
                return 'BEARISH'
            # Giá dưới EMA50 trong khi EMA còn bull → coi là bearish ngắn / chặn BUY
            if px < ema50 and (macd_h is None or macd_h <= 0):
                return 'BEARISH'
            if px > ema50 and (macd_h is None or macd_h >= 0):
                return 'BULLISH'
            return 'SIDEWAY'
        if px > ema50 and (macd_h is None or macd_h >= 0) and struct != 'BEARISH':
            return 'BULLISH'
        if px < ema50 and (macd_h is None or macd_h <= 0) and struct != 'BULLISH':
            return 'BEARISH'
        return struct if struct != 'SIDEWAY' else 'SIDEWAY'

    @classmethod
    def generate_market_analysis(cls, symbol_config: SymbolConfig) -> MarketForecast:
        symbol = symbol_config.symbol
        digits = int(getattr(symbol_config, 'digits', 5) or 5)
        timeframe = str(getattr(symbol_config, 'timeframe', None) or 'M15')
        strategy = str(getattr(symbol_config, 'strategy', None) or 'SMC_TREND')
        scalp = cls._is_scalp_mode(symbol_config)
        # Scalp dùng khung nhanh hơn nếu cấu hình M15 nhưng strategy scalp
        analysis_tf = timeframe
        if scalp and timeframe not in ('M1', 'M5'):
            analysis_tf = 'M5'

        rates = cls._load_rates(symbol, analysis_tf, 220)
        opens, highs, lows, closes = _extract_ohlc(rates or [])

        bid = _f(symbol_config.current_bid or symbol_config.current_price)
        ask = _f(symbol_config.current_ask or symbol_config.current_price)
        mid_px = (bid + ask) / 2.0 if bid > 0 and ask > 0 else _f(symbol_config.current_price)
        # Tín hiệu theo giá ĐÓNG nến — không dùng mid Ask/Bid (dễ lệch BUY khi spread)
        close_px = closes[-1] if closes else mid_px
        price = close_px if close_px > 0 else mid_px
        live_px = mid_px if mid_px > 0 else price

        spread = _f(symbol_config.current_spread_pips)
        if spread <= 0 and bid > 0 and ask > 0:
            point = _f(symbol_config.point_size) or (10 ** (-digits))
            pip = point * 10 if digits in (3, 5) else point
            if pip > 0:
                spread = round((ask - bid) / pip, 2)

        atr = _atr(highs, lows, closes, 14) if closes else None
        if atr is None or atr <= 0:
            atr = max(price * 0.001, 5 * (10 ** (-digits)))

        rsi = _rsi(closes, 14) if closes else None
        ema9_s = _ema(closes, 9) if closes else []
        ema21_s = _ema(closes, 21) if closes else []
        ema50_s = _ema(closes, 50) if closes else []
        ema200_s = _ema(closes, 200) if closes else []
        macd_h = _macd_hist(closes) if closes else None
        bb_mid, bb_up, bb_lo = _bollinger(closes, 20, 2.0) if closes else (None, None, None)
        r1, r2, s1, s2 = _swing_levels(highs, lows, 30 if not scalp else 15)

        ema9 = ema9_s[-1] if ema9_s and ema9_s[-1] is not None else None
        ema21 = ema21_s[-1] if ema21_s and ema21_s[-1] is not None else None
        ema50 = ema50_s[-1] if ema50_s and ema50_s[-1] is not None else None
        ema200 = ema200_s[-1] if ema200_s and ema200_s[-1] is not None else None

        # Fallback levels from ATR if swings missing
        if r1 is None:
            r1 = price + atr
            r2 = price + atr * 2
            s1 = price - atr
            s2 = price - atr * 2

        data_ok = len(closes) >= 50 and ema21 is not None and rsi is not None
        short_struct = _recent_structure_bias(highs, lows, closes, lookback=16) if data_ok else 'SIDEWAY'
        # Bias dài hạn theo EMA50/200 (không để impulse 3 nến đảo bias dài hạn)
        if data_ok and ema50 is not None and ema200 is not None:
            if ema50 > ema200 and price > ema50:
                long_bias = 'BULLISH'
            elif ema50 < ema200 and price < ema50:
                long_bias = 'BEARISH'
            elif price > ema50 and (macd_h is None or macd_h >= 0):
                long_bias = 'BULLISH'
            elif price < ema50 and (macd_h is None or macd_h <= 0):
                long_bias = 'BEARISH'
            else:
                long_bias = 'SIDEWAY'
        elif data_ok and ema50 is not None:
            long_bias = 'BULLISH' if price > ema50 else ('BEARISH' if price < ema50 else 'SIDEWAY')
        else:
            long_bias = 'SIDEWAY'

        htf_bias = cls._htf_bias(symbol, analysis_tf) if data_ok else None
        if htf_bias is None:
            htf_bias = long_bias

        # Luôn tính cả ngắn hạn (vào lệnh) và dài hạn (tham chiếu xu hướng + vùng giá)
        short_result = cls._decide_scalp(
            price=price, atr=atr, rsi=rsi, ema9=ema9, ema21=ema21,
            bb_mid=bb_mid, bb_up=bb_up, bb_lo=bb_lo,
            macd_h=macd_h, r1=r1, r2=r2, s1=s1, s2=s2,
            timeframe=analysis_tf, digits=digits, data_ok=data_ok,
            htf_bias=None,  # không để dài hạn chặn vào lệnh ngắn hạn
            struct_bias=short_struct,
            follow_short=True,
        )
        long_result = cls._decide_swing(
            price=price, atr=atr, rsi=rsi, ema50=ema50, ema200=ema200,
            macd_h=macd_h, r1=r1, r2=r2, s1=s1, s2=s2,
            timeframe=analysis_tf, digits=digits, data_ok=data_ok,
            struct_bias=long_bias,
        )

        # Bot luôn khớp theo ngắn hạn (làm mới theo trend hiện tại)
        result = short_result
        struct_bias = short_struct

        # Persist ATR / scan meta — UI dùng live; tín hiệu vẫn theo close
        try:
            symbol_config.atr_value = round(atr, digits)
            if spread > 0:
                symbol_config.current_spread_pips = spread
            if live_px > 0:
                symbol_config.current_price = Decimal(str(round(live_px, digits)))
            symbol_config.last_scanned_at = timezone.now()
            symbol_config.save(update_fields=[
                'atr_value', 'current_spread_pips', 'current_price', 'last_scanned_at'
            ])
        except Exception:
            pass

        def _horizon_pack(res: dict, label: str) -> dict:
            act = str(res.get('action') or '')
            direction = None
            if act == 'READY_TO_BUY':
                direction = 'BUY'
            elif act == 'READY_TO_SELL':
                direction = 'SELL'
            return {
                'label': label,
                'bias': res.get('trend_bias') or 'SIDEWAY',
                'action': act or 'MONITORING',
                'direction': direction,
                'confidence': res.get('confidence') or 0,
                'structure': res.get('structure') or '',
                'trigger': res.get('trigger') or '',
                'target_zone': res.get('target_zone') or '',
            }

        indicators_data = {
            'mode': 'SCALP' if scalp else 'SWING',
            'strategy': strategy,
            'analysis_tf': analysis_tf,
            'signal_price': round(price, digits),
            'htf_bias': htf_bias,
            'struct_bias': short_struct,
            'long_bias': long_bias,
            'rsi': rsi,
            'ema9': round(ema9, digits) if ema9 is not None else None,
            'ema21': round(ema21, digits) if ema21 is not None else None,
            'ema50': round(ema50, digits) if ema50 is not None else None,
            'ema200': round(ema200, digits) if ema200 is not None else None,
            'macd_hist': macd_h,
            'atr': round(atr, digits),
            'bb_mid': round(bb_mid, digits) if bb_mid is not None else None,
            'bb_upper': round(bb_up, digits) if bb_up is not None else None,
            'bb_lower': round(bb_lo, digits) if bb_lo is not None else None,
            'r1': round(r1, digits) if r1 is not None else None,
            'r2': round(r2, digits) if r2 is not None else None,
            's1': round(s1, digits) if s1 is not None else None,
            's2': round(s2, digits) if s2 is not None else None,
            'spread_pips': spread,
            'candles': len(closes),
            'data_ok': data_ok,
            'short_term': _horizon_pack(short_result, 'NGẮN HẠN'),
            'long_term': _horizon_pack(long_result, 'DÀI HẠN'),
            'exec_horizon': 'short',
            'signal_version': 2,
            'closed_candle_time': int(rates[-1]['time']) if rates else None,
        }

        for attempt in range(3):
            try:
                forecast, _ = MarketForecast.objects.update_or_create(
                    symbol=symbol,
                    defaults={
                        'timeframe': analysis_tf,
                        'trend_bias': result['trend_bias'],
                        'confidence_score': result['confidence'],
                        'current_price': Decimal(str(round(live_px if live_px > 0 else price, digits))),
                        'projected_target_zone': result['target_zone'],
                        'next_resistance_1': Decimal(str(round(result['r1'], digits))),
                        'next_resistance_2': Decimal(str(round(result['r2'], digits))),
                        'next_support_1': Decimal(str(round(result['s1'], digits))),
                        'next_support_2': Decimal(str(round(result['s2'], digits))),
                        'trigger_condition': result['trigger'],
                        'smc_structure': result['structure'],
                        'analysis_rationale': result['rationale'],
                        'recommended_action': result['action'],
                        'indicators_json': json.dumps(indicators_data),
                        'updated_at': timezone.now(),
                    },
                )
                return forecast
            except Exception as e:
                if attempt == 2:
                    logger.warning("MarketForecast update error for %s: %s", symbol, e)
                    return MarketForecast.objects.filter(symbol=symbol).first()
                time.sleep(0.1)

    @staticmethod
    def _decide_scalp(**kw) -> dict:
        """Ngắn hạn: EMA, giá, RSI, MACD phải đồng thuận; cấu trúc và S/R lọc lệnh."""
        price = kw['price']
        atr = kw['atr']
        rsi = kw['rsi']
        ema9 = kw['ema9']
        ema21 = kw['ema21']
        bb_up, bb_lo = kw['bb_up'], kw['bb_lo']
        macd_h = kw['macd_h']
        r1, r2, s1, s2 = kw['r1'], kw['r2'], kw['s1'], kw['s2']
        tf, digits, data_ok = kw['timeframe'], kw['digits'], kw['data_ok']
        struct_bias = kw.get('struct_bias') or 'SIDEWAY'

        def _base(bias, action, conf, structure, trigger, rationale):
            return {
                'trend_bias': bias,
                'action': action,
                'confidence': conf,
                'structure': structure,
                'trigger': trigger,
                'rationale': rationale,
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if (not data_ok or not all(v is not None and math.isfinite(float(v))
                                  for v in (price, atr, ema9, ema21, rsi, macd_h))
                or price <= 0 or atr <= 0):
            return _base(
                'SIDEWAY', 'MONITORING', 40.0,
                f"{tf} Chờ dữ liệu nến đủ (cần ≥50 nến MT5)",
                'Chưa đủ nến MT5 — không vào lệnh.',
                'Bot chỉ phân tích từ nến thật.',
            )

        note = (
            f" Struct={struct_bias} | EMA9={round(ema9, digits)} EMA21={round(ema21, digits)} "
            f"| RSI={rsi} MACD={macd_h} | R1={round(r1, digits) if r1 else '-'} "
            f"S1={round(s1, digits) if s1 else '-'}."
        )

        # Structure is a veto, never an override of timing confirmation.
        bull = (
            ema9 > ema21 and price >= ema9 and 50 <= rsi <= 70
            and macd_h > 0 and struct_bias != 'BEARISH'
        )
        bear = (
            ema9 < ema21 and price <= ema9 and 30 <= rsi <= 50
            and macd_h < 0 and struct_bias != 'BULLISH'
        )

        if bull and not bear:
            ok_sr, sr_note = _sr_entry_gate(price, atr, r1, r2, s1, s2, 'BUY')
            # Chỉ chờ khi sát kháng cự quá mức; còn lại vào luôn
            if not ok_sr:
                return _base(
                    'BULLISH', 'WAIT_FOR_PULLBACK', 60.0,
                    f"{tf} Sát R1 — chờ nhẹ rồi BUY",
                    f"Đang chờ điều kiện: giá rời kháng cự R1≈{round(r1, digits) if r1 else '-'} "
                    f"{sr_note}",
                    f"[SHORT {tf}] {sr_note}{note}",
                )
            conf = 70.0
            if macd_h is not None and macd_h > 0:
                conf += 8
            if struct_bias == 'BULLISH':
                conf += 8
            conf = min(92.0, conf)
            t_lo = round(price + atr * 0.5, digits)
            t_hi = round(min(price + atr * 1.3, r1) if r1 else price + atr * 1.3, digits)
            return {
                'trend_bias': 'BULLISH',
                'action': 'READY_TO_BUY',
                'confidence': round(conf, 1),
                'structure': f"{tf} SHORT BUY · Struct={struct_bias}",
                'trigger': (
                    f"Tín hiệu BUY đã xác nhận. Close={round(price, digits)}, EMA9>EMA21. "
                    f"Vùng giá chỉ tham khảo; lệnh cần qua kiểm tra rủi ro của ví.{note}"
                ),
                'rationale': f"[SHORT {tf}] Theo xu hướng ngắn hạn TĂNG.{note}",
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if bear and not bull:
            ok_sr, sr_note = _sr_entry_gate(price, atr, r1, r2, s1, s2, 'SELL')
            if not ok_sr:
                return _base(
                    'BEARISH', 'WAIT_FOR_PULLBACK', 60.0,
                    f"{tf} Sát S1 — chờ nhẹ rồi SELL",
                    f"Đang chờ điều kiện: giá rời hỗ trợ S1≈{round(s1, digits) if s1 else '-'} "
                    f"{sr_note}",
                    f"[SHORT {tf}] {sr_note}{note}",
                )
            conf = 70.0
            if macd_h is not None and macd_h < 0:
                conf += 8
            if struct_bias == 'BEARISH':
                conf += 8
            conf = min(92.0, conf)
            t_lo = round(max(price - atr * 1.3, s1) if s1 else price - atr * 1.3, digits)
            t_hi = round(price - atr * 0.5, digits)
            return {
                'trend_bias': 'BEARISH',
                'action': 'READY_TO_SELL',
                'confidence': round(conf, 1),
                'structure': f"{tf} SHORT SELL · Struct={struct_bias}",
                'trigger': (
                    f"Tín hiệu SELL đã xác nhận. Close={round(price, digits)}, EMA9<EMA21. "
                    f"Vùng giá chỉ tham khảo; lệnh cần qua kiểm tra rủi ro của ví.{note}"
                ),
                'rationale': f"[SHORT {tf}] Theo xu hướng ngắn hạn GIẢM.{note}",
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        bias = struct_bias if struct_bias in ('BULLISH', 'BEARISH') else (
            'BULLISH' if ema9 > ema21 else 'BEARISH' if ema9 < ema21 else 'SIDEWAY'
        )
        return _base(
            bias, 'WAIT_FOR_PULLBACK' if bias != 'SIDEWAY' else 'MONITORING', 52.0,
            f"{tf} Chờ xác nhận đồng thuận",
            'Chờ đủ EMA, giá, RSI và MACD cùng hướng trên nến đã đóng; '
            'cấu trúc giá không được bỏ qua bộ lọc.' + note,
            f"[SHORT {tf}] Chưa đủ xác nhận, không vào lệnh.{note}",
        )

    @staticmethod
    def _decide_swing(**kw) -> dict:
        """Dài hạn: EMA50/200 + cấu trúc gần nhất + S/R. Không BUY trong downtrend gần nhất."""
        price = kw['price']
        atr = kw['atr']
        rsi = kw['rsi']
        ema50 = kw['ema50']
        ema200 = kw['ema200']
        macd_h = kw['macd_h']
        r1, r2, s1, s2 = kw['r1'], kw['r2'], kw['s1'], kw['s2']
        tf, digits, data_ok = kw['timeframe'], kw['digits'], kw['data_ok']
        struct_bias = kw.get('struct_bias') or 'SIDEWAY'

        if not data_ok or ema50 is None or rsi is None:
            return {
                'trend_bias': 'SIDEWAY',
                'action': 'MONITORING',
                'confidence': 40.0,
                'structure': f"{tf} Chờ đủ nến (cần ≥50, tốt nhất ≥200 cho EMA200)",
                'trigger': 'Chưa đủ nến MT5 — không lập kế hoạch dài hạn.',
                'rationale': 'Phân tích dài hạn cần chuỗi nến thật từ terminal.',
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        up_trend = ema200 is not None and ema50 > ema200 and price > ema50
        down_trend = ema200 is not None and ema50 < ema200 and price < ema50
        if ema200 is None:
            up_trend = price > ema50 and (macd_h is None or macd_h >= 0) and rsi >= 50
            down_trend = price < ema50 and (macd_h is None or macd_h <= 0) and rsi <= 50

        # Cấu trúc gần nhất ghi đè khi mâu thuẫn với bounce ngắn
        if struct_bias == 'BEARISH':
            up_trend = False
            if price < ema50 or (macd_h is not None and macd_h <= 0):
                down_trend = True
        elif struct_bias == 'BULLISH':
            down_trend = False
            if price > ema50 or (macd_h is not None and macd_h >= 0):
                up_trend = True

        dist_ema = abs(price - ema50)
        extended = dist_ema > (1.5 * atr)
        # MACD histogram ngược hướng → chưa vào MARKET (tránh BUY khi MACD âm như case XAUUSD)
        macd_blocks_buy = macd_h is not None and macd_h < 0
        macd_blocks_sell = macd_h is not None and macd_h > 0
        sr_note_tail = f" R1={round(r1, digits) if r1 else '-'} S1={round(s1, digits) if s1 else '-'} Struct={struct_bias}."

        if up_trend:
            conf = 72.0
            if macd_h is not None and macd_h > 0:
                conf += 10
            if 45 <= rsi <= 68:
                conf += 6
            if struct_bias == 'BULLISH':
                conf += 4
            conf = min(94.0, conf)
            t_lo = round(price + atr * 1.5, digits)
            t_hi = round(min(price + atr * 3.0, r1) if r1 else price + atr * 3.0, digits)
            if extended and rsi > 65:
                return {
                    'trend_bias': 'BULLISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 5, 1),
                    'structure': f"{tf} Uptrend — giá quá xa EMA50, chờ về hỗ trợ",
                    'trigger': (
                        f"Chờ hồi về EMA50≈{round(ema50, digits)} hoặc S1≈{round(s1, digits) if s1 else '-'} rồi BUY."
                    ),
                    'rationale': (
                        f"[SWING {tf}] Uptrend nhưng cách EMA50 > 1.5×ATR. RSI={rsi}.{sr_note_tail}"
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            if macd_blocks_buy:
                return {
                    'trend_bias': 'BULLISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(max(55.0, conf - 12), 1),
                    'structure': f"{tf} Uptrend nhưng MACD_hist={macd_h} < 0 — chờ momentum",
                    'trigger': (
                        f"Xu hướng tăng (EMA50>EMA200) nhưng MACD histogram đang âm. "
                        f"Chờ MACD ≥ 0 và kiểm tra lại vùng giá, EMA50≈{round(ema50, digits)} / S1≈{round(s1, digits) if s1 else '-'}."
                    ),
                    'rationale': (
                        f"[SWING {tf}] Trend tăng nhưng timing xấu: MACD={macd_h}, RSI={rsi}. "
                        f"Giữ lệnh đang mở nếu có; không pyramid khi MACD ngược.{sr_note_tail}"
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            ok_sr, sr_note = _sr_entry_gate(price, atr, r1, r2, s1, s2, 'BUY')
            if not ok_sr:
                return {
                    'trend_bias': 'BULLISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 8, 1),
                    'structure': f"{tf} Uptrend nhưng sát kháng cự — chờ hồi",
                    'trigger': sr_note + f" Ưu tiên BUY gần S1≈{round(s1, digits) if s1 else '-'}.",
                    'rationale': f"[SWING {tf}] {sr_note}{sr_note_tail}",
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            return {
                'trend_bias': 'BULLISH',
                'action': 'READY_TO_BUY',
                'confidence': round(min(94.0, conf + (3 if sr_note else 0)), 1),
                'structure': f"{tf} Trend BUY: EMA50>EMA200 + MACD≥0 + Struct={struct_bias}",
                'trigger': (
                    f"DÀI HẠN BUY MARKET. Mục tiêu hướng R1={round(r1, digits) if r1 else '-'}. "
                    f"SL dưới S1={round(s1, digits) if s1 else '-'}. Trail SL khi thắng."
                    + (f" {sr_note}" if sr_note else '')
                ),
                'rationale': (
                    f"[SWING {tf}] Uptrend + MACD đồng thuận: giá {round(price, digits)} > EMA50={round(ema50, digits)}"
                    f"{f' > EMA200={round(ema200, digits)}' if ema200 else ''}, "
                    f"RSI={rsi}, MACD={macd_h}.{sr_note_tail}"
                ),
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if down_trend:
            conf = 72.0
            if macd_h is not None and macd_h < 0:
                conf += 10
            if 32 <= rsi <= 55:
                conf += 6
            if struct_bias == 'BEARISH':
                conf += 4
            conf = min(94.0, conf)
            t_lo = round(max(price - atr * 3.0, s1) if s1 else price - atr * 3.0, digits)
            t_hi = round(price - atr * 1.5, digits)
            if extended and rsi < 35:
                return {
                    'trend_bias': 'BEARISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 5, 1),
                    'structure': f"{tf} Downtrend — giá quá xa EMA50, chờ hồi lên kháng cự",
                    'trigger': (
                        f"Chờ hồi lên EMA50≈{round(ema50, digits)} hoặc R1≈{round(r1, digits) if r1 else '-'} rồi SELL."
                    ),
                    'rationale': (
                        f"[SWING {tf}] Downtrend nhưng cách EMA50 > 1.5×ATR. RSI={rsi}.{sr_note_tail}"
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            if macd_blocks_sell:
                return {
                    'trend_bias': 'BEARISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(max(55.0, conf - 12), 1),
                    'structure': f"{tf} Downtrend nhưng MACD_hist={macd_h} > 0 — chờ momentum",
                    'trigger': (
                        f"Xu hướng giảm nhưng MACD histogram đang dương. "
                        f"Chờ MACD ≤ 0 và kiểm tra lại vùng giá, R1≈{round(r1, digits) if r1 else '-'}."
                    ),
                    'rationale': (
                        f"[SWING {tf}] Trend giảm nhưng timing xấu: MACD={macd_h}, RSI={rsi}.{sr_note_tail}"
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            ok_sr, sr_note = _sr_entry_gate(price, atr, r1, r2, s1, s2, 'SELL')
            if not ok_sr:
                return {
                    'trend_bias': 'BEARISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 8, 1),
                    'structure': f"{tf} Downtrend nhưng sát hỗ trợ — chờ hồi",
                    'trigger': sr_note + f" Ưu tiên SELL gần R1≈{round(r1, digits) if r1 else '-'}.",
                    'rationale': f"[SWING {tf}] {sr_note}{sr_note_tail}",
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            return {
                'trend_bias': 'BEARISH',
                'action': 'READY_TO_SELL',
                'confidence': round(min(94.0, conf + (3 if sr_note else 0)), 1),
                'structure': f"{tf} Trend SELL: EMA50<EMA200 + MACD≤0 + Struct={struct_bias}",
                'trigger': (
                    f"DÀI HẠN SELL MARKET. Mục tiêu hướng S1={round(s1, digits) if s1 else '-'}. "
                    f"SL trên R1={round(r1, digits) if r1 else '-'}. Trail SL khi thắng."
                    + (f" {sr_note}" if sr_note else '')
                ),
                'rationale': (
                    f"[SWING {tf}] Downtrend + MACD đồng thuận: giá {round(price, digits)} < EMA50={round(ema50, digits)}"
                    f"{f' < EMA200={round(ema200, digits)}' if ema200 else ''}, "
                    f"RSI={rsi}, MACD={macd_h}.{sr_note_tail}"
                ),
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        # Struct bearish rõ nhưng EMA chưa đủ → vẫn chặn BUY, chờ SELL tại R
        if struct_bias == 'BEARISH':
            return {
                'trend_bias': 'BEARISH',
                'action': 'MONITORING',
                'confidence': 58.0,
                'structure': f"{tf} Cấu trúc gần nhất giảm — không BUY",
                'trigger': (
                    f"LH/LL gần đây. Không BUY. Chờ hồi lên R1≈{round(r1, digits) if r1 else '-'} để SELL."
                ),
                'rationale': f"[SWING {tf}] Struct BEARISH lọc ngược sóng.{sr_note_tail}",
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }
        if struct_bias == 'BULLISH':
            return {
                'trend_bias': 'BULLISH',
                'action': 'MONITORING',
                'confidence': 58.0,
                'structure': f"{tf} Cấu trúc gần nhất tăng — không SELL",
                'trigger': (
                    f"HH/HL gần đây. Không SELL. Chờ hồi về S1≈{round(s1, digits) if s1 else '-'} để BUY."
                ),
                'rationale': f"[SWING {tf}] Struct BULLISH lọc ngược sóng.{sr_note_tail}",
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        return {
            'trend_bias': 'SIDEWAY',
            'action': 'MONITORING',
            'confidence': 52.0,
            'structure': f"{tf} Sideway — chờ phá R1/S1",
            'trigger': (
                f"Không mở lệnh dài hạn. Theo dõi R1={round(r1, digits) if r1 else '-'} / "
                f"S1={round(s1, digits) if s1 else '-'}."
            ),
            'rationale': (
                f"[SWING {tf}] Sideway: EMA50={round(ema50, digits)}, "
                f"EMA200={round(ema200, digits) if ema200 else '-'}, RSI={rsi}.{sr_note_tail}"
            ),
            'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
            'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
        }

import json
import logging
import math
import time
from decimal import Decimal
from django.utils import timezone
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.analysis import config as cfg
from apps.analysis.market_structure import (
    BEARISH,
    BULLISH,
    SIDEWAYS,
    atr as _atr,
    breakout_state,
    build_zones,
    classify_trend,
    ema as _ema,
    nearest_zone,
    next_zone_beyond,
    retest_hold,
    structural_levels,
)

logger = logging.getLogger(__name__)


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


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


# Cache nến theo (symbol, timeframe): nến đã đóng không đổi trong chu kỳ nến nên
# vòng lặp bot 4s không phải gọi MT5 lại cho từng khung phụ.
_RATES_CACHE: dict[tuple[str, str], tuple[float, list]] = {}


def clear_rates_cache() -> None:
    """Xoá cache nến đa khung — dùng khi đổi phiên MT5 hoặc trong test."""
    _RATES_CACHE.clear()


TREND_LABEL_VI = {BULLISH: 'TĂNG', BEARISH: 'GIẢM', SIDEWAYS: 'ĐI NGANG'}
# TREND_CHOICES của MarketForecast dùng 'SIDEWAY', phân tích nội bộ dùng 'SIDEWAYS'.
_DB_TREND = {BULLISH: 'BULLISH', BEARISH: 'BEARISH', SIDEWAYS: 'SIDEWAY'}


def _zone_payload(zone: dict | None, digits: int) -> dict | None:
    if not zone:
        return None
    return {
        'low': round(zone['low'], digits),
        'high': round(zone['high'], digits),
        'mid': round(zone['mid'], digits),
        'touches': int(zone['touches']),
    }


def _tf_score_weights(entry_tf: str) -> dict[str, float]:
    """Trọng số điểm theo khung. Khung vào lệnh trùng khung context thì gộp trọng số."""
    weights: dict[str, float] = {}
    for slot, tf in (('H4', 'H4'), ('H1', 'H1'), ('M15', 'M15'), ('ENTRY', entry_tf)):
        weights[tf] = weights.get(tf, 0.0) + cfg.SCORE_WEIGHTS[slot]
    return weights


def _momentum_fraction(direction: str, momentum: dict) -> float:
    """Tỷ lệ chỉ báo động lượng khung vào lệnh ủng hộ hướng; chỉ báo thiếu thì bỏ qua."""
    checks = []
    ema9, ema21 = momentum.get('ema9'), momentum.get('ema21')
    if ema9 is not None and ema21 is not None:
        checks.append(ema9 > ema21 if direction == 'BUY' else ema9 < ema21)
    macd_h = momentum.get('macd_h')
    if macd_h is not None:
        checks.append(macd_h > 0 if direction == 'BUY' else macd_h < 0)
    rsi = momentum.get('rsi')
    if rsi is not None:
        checks.append(50 <= rsi <= 75 if direction == 'BUY' else 25 <= rsi <= 50)
    if not checks:
        return 0.0
    return sum(1 for ok in checks if ok) / len(checks)


def _direction_score(direction: str, packs: dict, entry_tf: str,
                     breakout: dict, momentum: dict) -> tuple[float, dict]:
    """Điểm 0-100 cho một hướng: đồng thuận đa khung + breakout + động lượng - phạt mâu thuẫn."""
    wanted = BULLISH if direction == 'BUY' else BEARISH
    earned = available = 0.0
    detail: dict[str, float | None] = {}

    for tf, weight in _tf_score_weights(entry_tf).items():
        pack = packs.get(tf)
        if not pack or not pack.get('sufficient'):
            detail[tf] = None  # thiếu nến → loại khỏi tổng rồi chuẩn hoá lại
            continue
        available += weight
        share = pack['confidence'] / 100.0
        if pack['trend'] == wanted:
            got = weight * share
        elif pack['trend'] == SIDEWAYS:
            got = weight * cfg.SIDEWAYS_SCORE_FRACTION * share
        else:
            got = 0.0
        earned += got
        detail[tf] = round(got, 1)

    available += cfg.SCORE_WEIGHTS['BREAKOUT']
    if breakout.get('confirmed') or breakout.get('retest'):
        got = cfg.SCORE_WEIGHTS['BREAKOUT']
    elif breakout.get('wick_only'):
        got = cfg.SCORE_WEIGHTS['BREAKOUT'] * cfg.WICK_ONLY_SCORE_FRACTION
    else:
        got = 0.0
    earned += got
    detail['breakout'] = round(got, 1)

    available += cfg.SCORE_WEIGHTS['MOMENTUM']
    got = cfg.SCORE_WEIGHTS['MOMENTUM'] * _momentum_fraction(direction, momentum)
    earned += got
    detail['momentum'] = round(got, 1)

    score = (earned / available * 100.0) if available > 0 else 0.0

    penalty = 0.0
    slot_tf = {'H4': 'H4', 'H1': 'H1', 'ENTRY': entry_tf}
    for (slot_a, slot_b), amount in cfg.CONFLICT_PENALTY.items():
        pack_a, pack_b = packs.get(slot_tf[slot_a]), packs.get(slot_tf[slot_b])
        if not pack_a or not pack_b:
            continue
        if not pack_a.get('sufficient') or not pack_b.get('sufficient'):
            continue
        trend_a, trend_b = pack_a['trend'], pack_b['trend']
        if trend_a in (BULLISH, BEARISH) and trend_b in (BULLISH, BEARISH) and trend_a != trend_b:
            penalty += amount
    detail['conflict_penalty'] = -round(penalty, 1)

    # D1 chỉ cung cấp bối cảnh trên UI. Tín hiệu vào lệnh bắt buộc dựa trên
    # entry timeframe + M15/H1/H4, nên D1 không cộng hoặc trừ điểm.
    detail['macro'] = 0.0

    score = max(0.0, min(100.0, score - penalty))
    detail['total'] = round(score, 1)
    return score, detail


def _retest_confirmed(rates: list, zones: list, price: float, tf_atr: float, direction: str) -> bool:
    """Retest hợp lệ: vùng vừa bị phá trong ít nến gần đây và nến đóng cuối vẫn giữ được."""
    if not rates or not zones or tf_atr <= 0:
        return False
    zone = nearest_zone(zones, price, 'support' if direction == 'BUY' else 'resistance')
    if not zone:
        return False
    window = rates[-(cfg.RETEST_LOOKBACK + 1):-1]
    if direction == 'BUY':
        was_other_side = any(_f(row.get('close')) < zone['high'] for row in window)
    else:
        was_other_side = any(_f(row.get('close')) > zone['low'] for row in window)
    return was_other_side and retest_hold(rates[-1], zone, tf_atr, direction)


def _overall_trend(packs: dict, entry_tf: str) -> str:
    """Xu hướng tổng: bỏ phiếu có trọng số theo khung, không copy trend khung nhỏ."""
    votes = {BULLISH: 0.0, BEARISH: 0.0, SIDEWAYS: 0.0}
    for tf, weight in _tf_score_weights(entry_tf).items():
        pack = packs.get(tf)
        if not pack or not pack.get('sufficient'):
            continue
        votes[pack['trend']] += weight * pack['confidence'] / 100.0
    if not any(votes.values()):
        return SIDEWAYS
    return max(votes.items(), key=lambda kv: kv[1])[0]


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
        tf = str(timeframe or 'M15').upper()
        key = (str(symbol), tf)
        ttl = cfg.RATES_CACHE_TTL.get(tf, 0.0)
        now = time.time()
        hit = _RATES_CACHE.get(key)
        if hit and ttl > 0 and now - hit[0] < ttl and len(hit[1]) >= count:
            return hit[1][-count:] if count else hit[1]
        rows = _closed_rates(TechnicalAnalyzer._load_raw_rates(symbol, tf, count + 1), tf)
        if rows and ttl > 0:
            _RATES_CACHE[key] = (now, rows)
        return rows

    @classmethod
    def _timeframe_pack(cls, symbol: str, timeframe: str) -> dict:
        """Phân tích độc lập một khung: xu hướng, cấu trúc, swing, ATR, nến đóng cuối."""
        rates = cls._load_rates(symbol, timeframe, cfg.RATES_COUNT)
        _opens, highs, lows, closes = _extract_ohlc(rates or [])
        tf_atr = _atr(highs, lows, closes, 14) if closes else None
        pack = classify_trend(highs, lows, closes, tf_atr)
        level_highs, level_lows = structural_levels(highs, lows)
        pack.update({
            'timeframe': str(timeframe).upper(),
            'atr': tf_atr,
            'swing_highs': level_highs,
            'swing_lows': level_lows,
            'last_closed': rates[-1] if rates else None,
            'prev_closed': rates[-2] if rates and len(rates) > 1 else None,
            'last_closed_time': int(rates[-1]['time']) if rates else None,
        })
        return pack

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

        # ---- Đa khung: khung vào lệnh + M15/H1/H4, thêm D1 khi đủ nến ----
        entry_tf = str(analysis_tf).upper()
        entry_pack = classify_trend(highs, lows, closes, atr)
        entry_level_highs, entry_level_lows = structural_levels(highs, lows)
        entry_pack.update({
            'timeframe': entry_tf,
            'atr': atr,
            'swing_highs': entry_level_highs,
            'swing_lows': entry_level_lows,
            'last_closed': rates[-1] if rates else None,
            'prev_closed': rates[-2] if rates and len(rates) > 1 else None,
            'last_closed_time': int(rates[-1]['time']) if rates else None,
        })
        packs = {entry_tf: entry_pack}
        for tf in cfg.CONTEXT_TIMEFRAMES:
            if tf not in packs:
                packs[tf] = cls._timeframe_pack(symbol, tf)
        macro_pack = cls._timeframe_pack(symbol, cfg.OPTIONAL_TIMEFRAME)
        if macro_pack.get('sufficient'):
            packs[cfg.OPTIONAL_TIMEFRAME] = macro_pack

        level_sets = {}
        for tf, pack in packs.items():
            levels = list(pack.get('swing_highs') or []) + list(pack.get('swing_lows') or [])
            if levels:
                level_sets[tf] = levels
        zones = build_zones(level_sets, cfg.ZONE_TF_WEIGHTS, cfg.ZONE_ATR_TOLERANCE * atr)

        # Breakout tính trên nến ĐÃ ĐÓNG so với vùng còn là cản ở nến liền trước.
        breakouts = {}
        prev_closed = entry_pack.get('prev_closed')
        prev_close_px = _f(prev_closed.get('close')) if prev_closed else 0.0
        for want in ('BUY', 'SELL'):
            side = 'resistance' if want == 'BUY' else 'support'
            broken = nearest_zone(zones, prev_close_px, side) if (zones and prev_close_px > 0) else None
            state = breakout_state(entry_pack.get('last_closed'), broken, atr, want)
            state['retest'] = _retest_confirmed(rates or [], zones, price, atr, want)
            breakouts[want] = state

        data_issues = []
        if not rates:
            data_issues.append('Không nhận được nến đã đóng hợp lệ từ MT5.')
        if not data_ok:
            data_issues.append(f'Chưa đủ nến {entry_tf} đã đóng để tính chỉ báo (cần ≥50).')

        mtf_result = cls._decide_multi_tf(
            entry_tf=entry_tf, packs=packs, price=price, atr=atr, digits=digits,
            zones=zones, breakouts=breakouts, data_issues=data_issues,
            momentum={'ema9': ema9, 'ema21': ema21, 'rsi': rsi, 'macd_h': macd_h},
        )
        # Kế hoạch lệnh chạy theo kết luận đa khung; ngắn hạn chỉ còn là lớp timing.
        result = mtf_result
        for key, fallback in (('r1', price + atr), ('r2', price + atr * 2),
                              ('s1', price - atr), ('s2', price - atr * 2)):
            if result.get(key) is None:
                result[key] = round(fallback, digits)
        if not result.get('target_zone'):
            result['target_zone'] = f"{result['s1']} - {result['r1']}"
        struct_bias = short_struct

        logger.debug(
            "[TradeAnalysis] %s | %s | bias=%s action=%s status=%s conf=%s | wait=%s",
            symbol,
            ' '.join(
                f"{tf}:{pack['trend'] if pack.get('sufficient') else 'N/A'}"
                for tf, pack in packs.items()
            ),
            result['trade_bias'], result['action'], result['setup_status'],
            result['confidence'], '; '.join(result['waiting_for']) or '-',
        )

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

        def _tf_row(tf: str) -> dict | None:
            pack = packs.get(tf)
            if not pack:
                return None
            return {
                'timeframe': tf,
                'trend': pack['trend'] if pack.get('sufficient') else None,
                'confidence': pack['confidence'] if pack.get('sufficient') else 0.0,
                'structure': pack.get('structure') or 'UNCLEAR',
                'candles': pack.get('candles') or 0,
                'sufficient': bool(pack.get('sufficient')),
                'ema20': round(pack['ema20'], digits) if pack.get('ema20') is not None else None,
                'ema50': round(pack['ema50'], digits) if pack.get('ema50') is not None else None,
                'ema200': round(pack['ema200'], digits) if pack.get('ema200') is not None else None,
                'last_closed_candle_time': pack.get('last_closed_time'),
            }

        timeframes_data = {tf: row for tf in packs if (row := _tf_row(tf))}
        row_order: list[str] = []
        for tf in (entry_tf, 'M15', 'H1', 'H4', cfg.OPTIONAL_TIMEFRAME):
            if tf in timeframes_data and tf not in row_order:
                row_order.append(tf)
        multi_tf_pack = {
            'label': 'XU HƯỚNG ĐA KHUNG',
            'entry_tf': entry_tf,
            'bias': _DB_TREND[result['overall_trend']],
            'overall_trend': result['overall_trend'],
            'trade_bias': result['trade_bias'],
            'action': result['action'],
            'setup_status': result['setup_status'],
            'confidence': result['confidence'],
            'structure': result['structure'],
            'trigger': result['trigger'],
            'target_zone': result['target_zone'],
            'rows': [timeframes_data[tf] for tf in row_order],
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
            # r1/s1 giữ tên cũ cho API/frontend nhưng nay là biên vùng cấu trúc thật,
            # không còn là signalPrice ± ATR. Dải ATR nằm ở atr_upper/lower_band.
            'r1': result['r1'],
            'r2': result['r2'],
            's1': result['s1'],
            's2': result['s2'],
            'atr_upper_band': result['atr_upper_band'],
            'atr_lower_band': result['atr_lower_band'],
            'support': _zone_payload(result['support'], digits),
            'resistance': _zone_payload(result['resistance'], digits),
            'spread_pips': spread,
            'candles': len(closes),
            'data_ok': data_ok,
            'short_term': _horizon_pack(short_result, 'NGẮN HẠN · VÀO LỆNH'),
            'long_term': multi_tf_pack,
            'multi_tf': multi_tf_pack,
            'timeframes': timeframes_data,
            'overall_trend': result['overall_trend'],
            'trade_bias': result['trade_bias'],
            'setup_status': result['setup_status'],
            'entry': result['entry'],
            'stop_loss': result['stop_loss'],
            'take_profit_1': result['take_profit_1'],
            'take_profit_2': result['take_profit_2'],
            'risk_reward': result['risk_reward'],
            'risk_reward_1': result['risk_reward_1'],
            'risk_reward_2': result['risk_reward_2'],
            'waiting_for': result['waiting_for'],
            'reasons': result['reasons'],
            'score_breakdown': result['score_breakdown'],
            'swing_long_term': _horizon_pack(long_result, 'SWING EMA50/200'),
            'exec_horizon': 'multi_tf',
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
                        'setup_status': result['setup_status'],
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
    def _decide_multi_tf(**kw) -> dict:
        """Kết luận đa khung (khung vào lệnh + M15/H1/H4, D1 tuỳ chọn).

        WAIT là trạng thái bình thường: chỉ trả BUY_READY/SELL_READY khi mọi điều
        kiện an toàn đều đạt, và chỉ khi đó mới sinh entry/SL/TP.
        """
        entry_tf = str(kw.get('entry_tf') or 'M15').upper()
        packs: dict = kw.get('packs') or {}
        price = _f(kw.get('price'))
        atr = _f(kw.get('atr'))
        digits = int(kw.get('digits') or 2)
        zones: list = list(kw.get('zones') or [])
        momentum: dict = kw.get('momentum') or {}
        breakouts: dict = kw.get('breakouts') or {}
        issues: list[str] = list(kw.get('data_issues') or [])

        price_ok = price > 0 and math.isfinite(price)
        atr_ok = atr > 0 and math.isfinite(atr)
        support = nearest_zone(zones, price, 'support') if zones and price_ok else None
        resistance = nearest_zone(zones, price, 'resistance') if zones and price_ok else None

        atr_upper = round(price + atr, digits) if price_ok and atr_ok else None
        atr_lower = round(price - atr, digits) if price_ok and atr_ok else None
        support_next = next_zone_beyond(zones, support, price, 'support')
        resistance_next = next_zone_beyond(zones, resistance, price, 'resistance')
        # r1/s1 giữ tên cũ cho tương thích API nhưng nay là biên vùng cấu trúc thật.
        # Khi giá ở biên cửa sổ quan sát (không còn vùng phía trước) thì dùng mục
        # tiêu đo theo ATR — vẫn khác dải ATR 1× hiển thị riêng bên dưới.
        measured_up = round(price + cfg.MEASURED_MOVE_ATR * atr, digits) if price_ok and atr_ok else None
        measured_down = round(price - cfg.MEASURED_MOVE_ATR * atr, digits) if price_ok and atr_ok else None
        r1 = round(resistance['low'], digits) if resistance else measured_up
        r2 = round(resistance_next['low'], digits) if resistance_next else (
            round(resistance['high'], digits) if resistance else measured_up)
        s1 = round(support['high'], digits) if support else measured_down
        s2 = round(support_next['high'], digits) if support_next else (
            round(support['low'], digits) if support else measured_down)

        def _tf_trend(tf: str) -> str:
            pack = packs.get(tf)
            if not pack or not pack.get('sufficient'):
                return 'thiếu nến'
            return f"{TREND_LABEL_VI[pack['trend']]} {pack['confidence']:.0f}"

        ordered_tfs = []
        for tf in (entry_tf, 'M15', 'H1', 'H4', cfg.OPTIONAL_TIMEFRAME):
            if tf in packs and tf not in ordered_tfs:
                ordered_tfs.append(tf)
        overall = _overall_trend(packs, entry_tf)
        structure = ' · '.join(f"{tf} {_tf_trend(tf)}" for tf in ordered_tfs)[:150]

        def _out(**over) -> dict:
            base = {
                'trend_bias': _DB_TREND[overall],
                'overall_trend': overall,
                'trade_bias': 'NEUTRAL',
                'action': 'MONITORING',
                'setup_status': 'WAITING',
                'confidence': 0.0,
                'structure': structure,
                'trigger': '',
                'rationale': '',
                'target_zone': f"{s1} - {r1}" if s1 is not None and r1 is not None else '',
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                'support': support, 'resistance': resistance,
                'atr_upper_band': atr_upper, 'atr_lower_band': atr_lower,
                'entry': None, 'stop_loss': None,
                'take_profit_1': None, 'take_profit_2': None,
                'risk_reward': None, 'risk_reward_1': None, 'risk_reward_2': None,
                'waiting_for': [], 'reasons': [], 'score_breakdown': {},
            }
            base.update(over)
            return base

        if not price_ok:
            issues.append('Giá tín hiệu không hợp lệ.')
        if not atr_ok:
            issues.append('ATR không hợp lệ (NaN/0) — không lập kế hoạch giá.')
        for tf in ('H1', 'H4'):
            pack = packs.get(tf)
            if not pack or not pack.get('sufficient'):
                issues.append(f'Thiếu dữ liệu nến {tf} (cần ≥{cfg.MIN_CANDLES_STRUCTURE} nến đã đóng).')
        entry_pack = packs.get(entry_tf)
        if not entry_pack or not entry_pack.get('sufficient'):
            issues.append(f'Thiếu dữ liệu nến {entry_tf} để xác định cấu trúc.')
        if not zones:
            issues.append('Chưa dựng được vùng cấu trúc nào từ nến đã đóng.')

        if issues:
            return _out(
                trigger='Dữ liệu chưa đủ để ra tín hiệu: ' + ' '.join(issues),
                rationale='[MTF] ' + ' '.join(issues),
                reasons=issues,
                waiting_for=issues,
            )

        buy_score, buy_detail = _direction_score(
            'BUY', packs, entry_tf, breakouts.get('BUY') or {}, momentum)
        sell_score, sell_detail = _direction_score(
            'SELL', packs, entry_tf, breakouts.get('SELL') or {}, momentum)
        direction = 'BUY' if buy_score >= sell_score else 'SELL'
        confidence = round(max(buy_score, sell_score), 1)
        detail = buy_detail if direction == 'BUY' else sell_detail
        breakout = breakouts.get(direction) or {}
        wanted = BULLISH if direction == 'BUY' else BEARISH
        sign = 1 if direction == 'BUY' else -1

        sl_zone = support if direction == 'BUY' else resistance
        tp_zone = resistance if direction == 'BUY' else support
        next_target = resistance_next if direction == 'BUY' else support_next
        measured = measured_up if direction == 'BUY' else measured_down

        entry = round(price, digits)
        stop_loss = None
        risk = 0.0
        if sl_zone is not None:
            stop_loss = round(
                sl_zone['low'] - cfg.ATR_SL_BUFFER * atr if direction == 'BUY'
                else sl_zone['high'] + cfg.ATR_SL_BUFFER * atr, digits)
            risk = sign * (entry - stop_loss)
        take_profit_1 = round(
            (tp_zone['low'] if direction == 'BUY' else tp_zone['high']) if tp_zone else measured,
            digits)
        reward = sign * (take_profit_1 - entry)
        if next_target:
            take_profit_2 = round(next_target['low'] if direction == 'BUY' else next_target['high'], digits)
        elif risk > 0:
            take_profit_2 = round(entry + sign * risk * cfg.TP2_RR_FALLBACK, digits)
        else:
            take_profit_2 = round(entry + sign * cfg.MEASURED_MOVE_ATR * atr * cfg.TP2_RR_FALLBACK, digits)
        if sign * (take_profit_2 - take_profit_1) <= 0:
            take_profit_2 = round(
                take_profit_1 + sign * max(risk, cfg.MEASURED_MOVE_ATR * atr / 2), digits)
        reward_2 = sign * (take_profit_2 - entry)
        risk_reward_1 = round(reward / risk, 2) if risk > 0 else None
        risk_reward_2 = round(reward_2 / risk, 2) if risk > 0 else None
        valid_rr = [
            value for value in (risk_reward_1, risk_reward_2)
            if value is not None and math.isfinite(value)
        ]
        # TP1 là mục tiêu chốt một phần. Setup vẫn hợp lệ nếu TP2 có đủ RR.
        risk_reward = max(valid_rr) if valid_rr else None

        reasons = [f"{tf}: {_tf_trend(tf)}" for tf in ordered_tfs]
        reasons.append(f"Xu hướng tổng: {TREND_LABEL_VI[overall]}")
        if detail.get('conflict_penalty', 0) < 0:
            reasons.append(f"Trừ {abs(detail['conflict_penalty']):.0f} điểm do các khung mâu thuẫn")
        macro = packs.get(cfg.OPTIONAL_TIMEFRAME)
        if macro and macro.get('sufficient'):
            reasons.append(
                f"{cfg.OPTIONAL_TIMEFRAME}: {TREND_LABEL_VI[macro['trend']]} "
                "(chỉ tham khảo, không tính điểm)"
            )

        waiting: list[str] = []
        breakout_missing = False
        if sl_zone is None:
            side_txt = 'hỗ trợ bên dưới' if direction == 'BUY' else 'kháng cự bên trên'
            waiting.append(f"Chưa có vùng {side_txt} để đặt SL theo cấu trúc.")
        h4 = packs['H4']
        if h4['trend'] not in (wanted, SIDEWAYS):
            waiting.append(f"H4 đang {TREND_LABEL_VI[h4['trend']]} — chờ H4 thôi ngược hướng {direction}.")
        h1 = packs['H1']
        if h1['trend'] not in (wanted, SIDEWAYS):
            waiting.append(
                f"H1 đang {TREND_LABEL_VI[h1['trend']]} — chờ H1 thôi ngược hướng {direction}."
            )
        if cfg.REQUIRE_BREAKOUT_CLOSE and not (breakout.get('confirmed') or breakout.get('retest')):
            breakout_missing = True
            side_txt = 'trên' if direction == 'BUY' else 'dưới'
            level = breakout.get('level')
            if level is None:
                waiting.append(f"{entry_tf} phá vùng cấu trúc bằng nến đã đóng.")
            elif breakout.get('wick_only'):
                waiting.append(
                    f"{entry_tf} mới xuyên bằng râu nến — cần nến ĐÓNG {side_txt} {round(level, digits)}."
                )
            else:
                waiting.append(f"{entry_tf} đóng cửa {side_txt} {round(level, digits)}.")
        if risk_reward is None or risk_reward < cfg.MIN_RISK_REWARD:
            rr_txt = f"{risk_reward:.2f}" if risk_reward is not None else 'không tính được'
            waiting.append(
                f"Risk/Reward tốt nhất giữa TP1/TP2 là {rr_txt} < {cfg.MIN_RISK_REWARD}."
            )
        if reward < cfg.MIN_ROOM_ATR * atr:
            waiting.append(
                f"Còn dưới {cfg.MIN_ROOM_ATR}×ATR khoảng trống tới vùng cản gần nhất — giá quá sát cản."
            )
        level = breakout.get('level')
        if level is not None and abs(price - level) > cfg.MAX_EXTENSION_ATR * atr:
            waiting.append(
                f"Giá đã chạy quá {cfg.MAX_EXTENSION_ATR}×ATR khỏi vùng phá vỡ — chờ nhịp hồi."
            )
        if confidence < cfg.MIN_CONFIDENCE:
            waiting.append(
                f"Điểm đồng thuận {confidence:.0f}/100 dưới ngưỡng {cfg.MIN_CONFIDENCE:.0f}."
            )

        if not waiting:
            status = 'BUY_READY' if direction == 'BUY' else 'SELL_READY'
            action = 'READY_TO_BUY' if direction == 'BUY' else 'READY_TO_SELL'
            trigger = (
                f"{direction} xác nhận: {entry_tf} đóng cửa qua {round(level, digits) if level is not None else '-'}, "
                f"H1 {TREND_LABEL_VI[h1['trend']]}, H4 {TREND_LABEL_VI[h4['trend']]}. "
                f"Entry≈{entry}, SL={stop_loss}, TP1={take_profit_1}, TP2={take_profit_2}, "
                f"RR1={risk_reward_1:.2f}, RR2={risk_reward_2:.2f}. "
                f"Vùng SL/TP này là kế hoạch phân tích; "
                f"lệnh thật vẫn qua kiểm tra rủi ro USD của ví."
            )
            return _out(
                trade_bias=direction, action=action, setup_status=status,
                confidence=confidence, trigger=trigger,
                rationale=f"[MTF {entry_tf}/H1/H4] " + ' | '.join(reasons),
                target_zone=f"{take_profit_1} - {take_profit_2}",
                entry=entry, stop_loss=stop_loss,
                take_profit_1=take_profit_1, take_profit_2=take_profit_2,
                risk_reward=risk_reward, risk_reward_1=risk_reward_1,
                risk_reward_2=risk_reward_2, reasons=reasons, waiting_for=[],
                score_breakdown=detail,
            )

        if confidence >= cfg.WATCHING_CONFIDENCE:
            status = 'WATCHING_BUY' if direction == 'BUY' else 'WATCHING_SELL'
            action = 'BREAKOUT_PENDING' if (breakout_missing and len(waiting) == 1) else 'WAIT_FOR_PULLBACK'
            trade_bias = direction
        else:
            status, action, trade_bias = 'WAITING', 'MONITORING', 'NEUTRAL'

        return _out(
            trade_bias=trade_bias, action=action, setup_status=status,
            confidence=confidence,
            trigger='Đang chờ: ' + ' '.join(f'({i + 1}) {w}' for i, w in enumerate(waiting)),
            rationale=f"[MTF {entry_tf}/H1/H4] " + ' | '.join(reasons),
            reasons=reasons, waiting_for=waiting, score_breakdown=detail,
        )

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

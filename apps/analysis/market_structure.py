"""Cấu trúc thị trường: swing, xu hướng theo điểm, vùng S/R thật, xác nhận breakout.

Toàn bộ là hàm thuần trên list OHLC nên test được mà không cần MT5 hay Django.
Không có mức giá cứng: mọi ngưỡng đều theo ATR hoặc theo chính dữ liệu nến.
"""
import math

from apps.analysis import config as cfg

BULLISH = 'BULLISH'
BEARISH = 'BEARISH'
SIDEWAYS = 'SIDEWAYS'


def ema(values: list[float], period: int) -> list[float | None]:
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


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def find_swing_points(highs: list[float], lows: list[float], strength: int = cfg.SWING_STRENGTH):
    """Fractal swing: đỉnh khi `strength` nến mỗi bên đều thấp hơn (và ngược lại)."""
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    n = min(len(highs), len(lows))
    if n < strength * 2 + 1:
        return swing_highs, swing_lows
    for i in range(strength, n - strength):
        left = range(i - strength, i)
        right = range(i + 1, i + strength + 1)
        if all(highs[i] >= highs[j] for j in left) and all(highs[i] > highs[j] for j in right):
            swing_highs.append((i, highs[i]))
        if all(lows[i] <= lows[j] for j in left) and all(lows[i] < lows[j] for j in right):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def structural_levels(highs: list[float], lows: list[float],
                      lookback: int = cfg.SWING_LOOKBACK,
                      strength: int = cfg.SWING_STRENGTH,
                      limit: int = cfg.SWING_ZONE_LIMIT) -> tuple[list[float], list[float]]:
    """Mức cấu trúc gần nhất của một khung.

    Ưu tiên fractal swing. Trend chạy mượt có thể không tạo đủ swing được xác
    nhận — khi đó dùng cực trị từng đoạn của cửa sổ, vẫn hoàn toàn từ nến thật.
    """
    window_highs = highs[-lookback:]
    window_lows = lows[-lookback:]
    swing_highs, swing_lows = find_swing_points(window_highs, window_lows, strength)
    level_highs = [price for _i, price in swing_highs][-limit:]
    level_lows = [price for _i, price in swing_lows][-limit:]
    if len(level_highs) >= 2 and len(level_lows) >= 2:
        return level_highs, level_lows

    # Bỏ vài nến cuối: chúng chưa đủ nến bên phải để xác nhận, nếu lấy sẽ biến
    # chính nến hiện tại thành "kháng cự" của nó.
    settled_highs = window_highs[:-strength] if len(window_highs) > strength * 3 else window_highs
    settled_lows = window_lows[:-strength] if len(window_lows) > strength * 3 else window_lows
    segment = max(len(settled_highs) // max(cfg.EXTREME_SEGMENTS, 1), strength * 2 + 1)
    for start in range(0, len(settled_highs), segment):
        chunk_highs = settled_highs[start:start + segment]
        chunk_lows = settled_lows[start:start + segment]
        if chunk_highs:
            level_highs.append(max(chunk_highs))
        if chunk_lows:
            level_lows.append(min(chunk_lows))
    return level_highs[-limit:], level_lows[-limit:]


def structure_label(swing_highs, swing_lows) -> str:
    """HH/HL, LH/LL hay hỗn hợp — đọc từ hai swing gần nhất mỗi phía."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return 'UNCLEAR'
    higher_high = swing_highs[-1][1] > swing_highs[-2][1]
    higher_low = swing_lows[-1][1] > swing_lows[-2][1]
    lower_high = swing_highs[-1][1] < swing_highs[-2][1]
    lower_low = swing_lows[-1][1] < swing_lows[-2][1]
    if higher_high and higher_low:
        return 'HH/HL'
    if lower_high and lower_low:
        return 'LH/LL'
    if higher_high or higher_low:
        return 'MIXED_UP'
    if lower_high or lower_low:
        return 'MIXED_DOWN'
    return 'RANGE'


def structure_direction(label: str | None) -> str:
    """Hướng của cấu trúc swing mạnh; mixed/range/unclear là trung tính."""
    if label == 'HH/HL':
        return BULLISH
    if label == 'LH/LL':
        return BEARISH
    return SIDEWAYS


def select_trend(scores: dict[str, float]) -> tuple[str, float]:
    """Chọn trend và độ chắc chắn của chính phân loại đó.

    Khi điểm tăng/giảm gần như hòa nhau, confidence phải phản ánh độ không chắc
    chắn thay vì nhảy lên 100. Confidence này không phải xác suất thắng của lệnh.
    """
    bullish = float(scores.get('bullish') or 0.0)
    bearish = float(scores.get('bearish') or 0.0)
    sideways = float(scores.get('sideways') or 0.0)
    directional = sorted(
        ((bullish, BULLISH), (bearish, BEARISH)),
        key=lambda item: item[0],
        reverse=True,
    )
    leader_score, leader = directional[0]
    directional_gap = abs(bullish - bearish)

    if sideways >= leader_score:
        confidence = max(sideways, 50.0 - directional_gap)
        return SIDEWAYS, min(100.0, confidence)
    if directional_gap < 10.0:
        # Hai hướng giằng co sát nhau: SIDEWAYS nhưng không được gán certainty 100.
        confidence = max(sideways, 50.0 - directional_gap)
        return SIDEWAYS, min(100.0, confidence)
    return leader, min(100.0, leader_score)


def classify_trend(highs: list[float], lows: list[float], closes: list[float],
                   tf_atr: float | None = None) -> dict:
    """Chấm điểm xu hướng một khung từ swing + EMA20/50/200 + độ dốc EMA.

    Thành phần nào thiếu nến thì bị loại khỏi tổng trọng số rồi chuẩn hoá lại,
    nên thiếu EMA200 không bị hiểu nhầm thành tăng hoặc giảm.
    """
    n = len(closes)
    result = {
        'trend': SIDEWAYS,
        'confidence': 0.0,
        'structure': 'UNCLEAR',
        'scores': {'bullish': 0.0, 'bearish': 0.0, 'sideways': 0.0},
        'ema20': None, 'ema50': None, 'ema200': None,
        'candles': n,
        'components': [],
        'sufficient': False,
    }
    if n < cfg.MIN_CANDLES_STRUCTURE:
        return result

    price = closes[-1]
    tf_atr = tf_atr if (tf_atr and math.isfinite(tf_atr) and tf_atr > 0) else atr(highs, lows, closes)
    if not tf_atr or not math.isfinite(tf_atr) or tf_atr <= 0:
        tf_atr = max(abs(price) * 1e-4, 1e-9)

    ema20_s = ema(closes, 20)
    ema50_s = ema(closes, 50) if n >= cfg.MIN_CANDLES_EMA50 else []
    ema200_s = ema(closes, 200) if n >= cfg.MIN_CANDLES_EMA200 else []
    ema20 = ema20_s[-1] if ema20_s else None
    ema50 = ema50_s[-1] if ema50_s else None
    ema200 = ema200_s[-1] if ema200_s else None

    swing_highs, swing_lows = find_swing_points(
        highs[-cfg.SWING_LOOKBACK:], lows[-cfg.SWING_LOOKBACK:]
    )
    label = structure_label(swing_highs, swing_lows)

    bull = bear = side = 0.0
    total = 0.0
    components: list[str] = []

    def add(weight: float, verdict: str, note: str):
        nonlocal bull, bear, side, total
        total += weight
        if verdict == BULLISH:
            bull += weight
        elif verdict == BEARISH:
            bear += weight
        else:
            side += weight
        components.append(note)

    structure_verdict = {
        'HH/HL': BULLISH, 'MIXED_UP': BULLISH,
        'LH/LL': BEARISH, 'MIXED_DOWN': BEARISH,
    }.get(label, SIDEWAYS)
    structure_weight = 3.0 if label in ('HH/HL', 'LH/LL') else 2.0
    add(structure_weight, structure_verdict, f'structure={label}')

    if ema20 is not None:
        add(1.0, BULLISH if price > ema20 else BEARISH if price < ema20 else SIDEWAYS,
            f'price{"^" if price > ema20 else "v"}EMA20')
    if ema50 is not None:
        add(1.0, BULLISH if price > ema50 else BEARISH if price < ema50 else SIDEWAYS,
            f'price{"^" if price > ema50 else "v"}EMA50')
    if ema20 is not None and ema50 is not None:
        gap = ema20 - ema50
        verdict = SIDEWAYS if abs(gap) < cfg.FLAT_SLOPE_ATR * tf_atr else (BULLISH if gap > 0 else BEARISH)
        add(2.0, verdict, f'EMA20{"^" if gap > 0 else "v"}EMA50')
    if ema50 is not None and ema200 is not None:
        add(2.0, BULLISH if ema50 > ema200 else BEARISH if ema50 < ema200 else SIDEWAYS,
            f'EMA50{"^" if ema50 > ema200 else "v"}EMA200')
    if ema20_s and len(ema20_s) > cfg.EMA_SLOPE_LOOKBACK:
        prev = ema20_s[-1 - cfg.EMA_SLOPE_LOOKBACK]
        if prev is not None and ema20 is not None:
            slope = ema20 - prev
            verdict = SIDEWAYS if abs(slope) < cfg.FLAT_SLOPE_ATR * tf_atr else (
                BULLISH if slope > 0 else BEARISH)
            add(1.0, verdict, f'slopeEMA20={round(slope, 5)}')

    if total <= 0:
        return result

    scores = {'bullish': bull / total * 100.0, 'bearish': bear / total * 100.0,
              'sideways': side / total * 100.0}
    trend, confidence = select_trend(scores)

    result.update({
        'trend': trend,
        'confidence': round(confidence, 1),
        'structure': label,
        'scores': {k: round(v, 1) for k, v in scores.items()},
        'ema20': ema20, 'ema50': ema50, 'ema200': ema200,
        'components': components,
        'sufficient': True,
    })
    return result


def cluster_levels(levels: list[tuple[float, float]], tolerance: float) -> list[dict]:
    """Gom các mức giá cách nhau <= tolerance thành vùng {low, high, mid, touches, weight}."""
    clean = [(p, w) for p, w in levels if p is not None and math.isfinite(p) and p > 0]
    if not clean:
        return []
    clean.sort(key=lambda x: x[0])
    tolerance = max(tolerance, 1e-9)
    zones: list[dict] = []
    for price, weight in clean:
        if zones and price - zones[-1]['high'] <= tolerance:
            zone = zones[-1]
            zone['high'] = max(zone['high'], price)
            zone['touches'] += 1
            zone['weight'] += weight
        else:
            zones.append({'low': price, 'high': price, 'touches': 1, 'weight': weight})
    for zone in zones:
        zone['mid'] = (zone['low'] + zone['high']) / 2.0
    return zones


def build_zones(level_sets: dict[str, list[float]], tf_weights: dict[str, float],
                tolerance: float) -> list[dict]:
    """Gom swing của nhiều khung thành vùng chung; khung lớn có trọng số cao hơn."""
    levels: list[tuple[float, float]] = []
    for tf, prices in level_sets.items():
        weight = tf_weights.get(tf, 1.0)
        levels.extend((p, weight) for p in prices)
    return cluster_levels(levels, tolerance)


def nearest_zone(zones: list[dict], price: float, side: str) -> dict | None:
    """Vùng kháng cự gần nhất phía trên giá, hoặc hỗ trợ gần nhất phía dưới."""
    if side == 'resistance':
        above = [z for z in zones if z['high'] > price]
        return min(above, key=lambda z: z['high'] - price) if above else None
    below = [z for z in zones if z['low'] < price]
    return max(below, key=lambda z: z['low']) if below else None


def next_zone_beyond(zones: list[dict], zone: dict | None, price: float, side: str) -> dict | None:
    """Vùng cấu trúc kế tiếp sau `zone` — dùng làm TP2."""
    if zone is None:
        return None
    if side == 'resistance':
        further = [z for z in zones if z['low'] > zone['high']]
        return min(further, key=lambda z: z['low']) if further else None
    further = [z for z in zones if z['high'] < zone['low']]
    return max(further, key=lambda z: z['high']) if further else None


def breakout_state(candle: dict | None, zone: dict | None, tf_atr: float,
                   direction: str) -> dict:
    """Trạng thái phá vùng của NẾN ĐÃ ĐÓNG cuối cùng.

    `confirmed` chỉ True khi giá ĐÓNG vượt biên vùng cộng đệm ATR; râu nến vượt
    mà thân chưa đóng qua chỉ cho `wick_only`.
    """
    state = {
        'confirmed': False,
        'wick_only': False,
        'weak_close': False,
        'quality_ok': False,
        'level': None,
        'distance': None,
        'body_atr': None,
        'body_ratio': None,
        'rejection_wick_ratio': None,
        'failure_reasons': [],
    }
    if not candle or not zone or not tf_atr or tf_atr <= 0:
        return state
    try:
        open_price = float(candle['open'])
        close = float(candle['close'])
        high = float(candle['high'])
        low = float(candle['low'])
    except (KeyError, TypeError, ValueError):
        return state
    buffer_px = cfg.BREAKOUT_BUFFER * tf_atr
    candle_range = high - low
    if candle_range <= 0:
        state['failure_reasons'] = ['invalid_range']
        return state
    body = abs(close - open_price)
    body_atr = body / tf_atr
    body_ratio = body / candle_range
    if direction == 'BUY':
        directional_body = close > open_price
        rejection_wick = high - max(open_price, close)
    else:
        directional_body = close < open_price
        rejection_wick = min(open_price, close) - low
    rejection_wick_ratio = max(0.0, rejection_wick) / candle_range
    failures = []
    if not directional_body:
        failures.append('wrong_body_direction')
    if body_atr < cfg.BREAKOUT_MIN_BODY_ATR:
        failures.append('body_too_small_atr')
    if body_ratio < cfg.BREAKOUT_MIN_BODY_RATIO:
        failures.append('body_ratio_too_small')
    if rejection_wick_ratio > cfg.BREAKOUT_MAX_REJECTION_WICK_RATIO:
        failures.append('excessive_rejection_wick')
    state.update({
        'body_atr': round(body_atr, 4),
        'body_ratio': round(body_ratio, 4),
        'rejection_wick_ratio': round(rejection_wick_ratio, 4),
        'quality_ok': not failures,
        'failure_reasons': failures,
    })

    if direction == 'BUY':
        level = zone['high']
        state['level'] = level
        state['distance'] = close - level
        if close > level + buffer_px:
            state['confirmed'] = state['quality_ok']
            state['weak_close'] = not state['quality_ok']
        elif high > level:
            state['wick_only'] = True
    elif direction == 'SELL':
        level = zone['low']
        state['level'] = level
        state['distance'] = level - close
        if close < level - buffer_px:
            state['confirmed'] = state['quality_ok']
            state['weak_close'] = not state['quality_ok']
        elif low < level:
            state['wick_only'] = True
    return state


def retest_hold(candle: dict | None, zone: dict | None, tf_atr: float, direction: str) -> bool:
    """Nến đã đóng quay lại chạm vùng vừa phá nhưng vẫn giữ được phía bên kia."""
    if not candle or not zone or not tf_atr or tf_atr <= 0:
        return False
    try:
        close = float(candle['close'])
        high = float(candle['high'])
        low = float(candle['low'])
    except (KeyError, TypeError, ValueError):
        return False
    buffer_px = cfg.BREAKOUT_BUFFER * tf_atr
    if direction == 'BUY':
        return low <= zone['high'] + buffer_px and close > zone['high'] + buffer_px
    if direction == 'SELL':
        return high >= zone['low'] - buffer_px and close < zone['low'] - buffer_px
    return False

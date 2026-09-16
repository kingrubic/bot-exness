import json
import logging
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
        return 100.0
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
    - Lướt sóng (SCALPING_BB / M1-M5): EMA9/21 + RSI + MACD, lọc HTF — không BUY ngược downtrend.
    - Dài hạn / trend (SMC_TREND, H1+): EMA50/200 + MACD; trail SL theo ví.
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
        try:
            from apps.trading.mt5_session import MT5NativeSession
            rates = MT5NativeSession.copy_rates(symbol, timeframe or 'M15', count)
            if rates and len(rates) >= 30:
                return rates
        except Exception as e:
            logger.debug("copy_rates %s %s: %s", symbol, timeframe, e)
        return None

    @staticmethod
    def _htf_bias(symbol: str, scalp_tf: str) -> str | None:
        """Xu hướng khung lớn: M1→M5, M5→M15, khác→H1. Chặn vào ngược sóng."""
        tf = str(scalp_tf or 'M5').upper()
        htf = {'M1': 'M5', 'M5': 'M15', 'M15': 'H1'}.get(tf, 'H1')
        rates = TechnicalAnalyzer._load_rates(symbol, htf, 220)
        _o, _h, _l, closes = _extract_ohlc(rates or [])
        if len(closes) < 60:
            return None
        ema50_s = _ema(closes, 50)
        ema200_s = _ema(closes, 200)
        ema50 = ema50_s[-1] if ema50_s else None
        ema200 = ema200_s[-1] if ema200_s else None
        px = closes[-1]
        macd_h = _macd_hist(closes)
        if ema50 is None:
            return None
        if ema200 is not None:
            if ema50 > ema200 and px > ema50:
                return 'BULLISH'
            if ema50 < ema200 and px < ema50:
                return 'BEARISH'
            return 'SIDEWAY'
        if px > ema50 and (macd_h is None or macd_h >= 0):
            return 'BULLISH'
        if px < ema50 and (macd_h is None or macd_h <= 0):
            return 'BEARISH'
        return 'SIDEWAY'

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
        htf_bias = cls._htf_bias(symbol, analysis_tf) if scalp and data_ok else None

        if scalp:
            result = cls._decide_scalp(
                price=price, atr=atr, rsi=rsi, ema9=ema9, ema21=ema21,
                bb_mid=bb_mid, bb_up=bb_up, bb_lo=bb_lo,
                macd_h=macd_h, r1=r1, r2=r2, s1=s1, s2=s2,
                timeframe=analysis_tf, digits=digits, data_ok=data_ok,
                htf_bias=htf_bias,
            )
        else:
            result = cls._decide_swing(
                price=price, atr=atr, rsi=rsi, ema50=ema50, ema200=ema200,
                macd_h=macd_h, r1=r1, r2=r2, s1=s1, s2=s2,
                timeframe=analysis_tf, digits=digits, data_ok=data_ok,
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

        indicators_data = {
            'mode': 'SCALP' if scalp else 'SWING',
            'strategy': strategy,
            'analysis_tf': analysis_tf,
            'signal_price': round(price, digits),
            'htf_bias': htf_bias,
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
            'spread_pips': spread,
            'candles': len(closes),
            'data_ok': data_ok,
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
        """EMA9/21 + RSI + MACD; chặn BUY khi HTF giảm / SELL khi HTF tăng."""
        price = kw['price']
        atr = kw['atr']
        rsi = kw['rsi']
        ema9 = kw['ema9']
        ema21 = kw['ema21']
        bb_up, bb_lo = kw['bb_up'], kw['bb_lo']
        macd_h = kw['macd_h']
        r1, r2, s1, s2 = kw['r1'], kw['r2'], kw['s1'], kw['s2']
        tf, digits, data_ok = kw['timeframe'], kw['digits'], kw['data_ok']
        htf_bias = kw.get('htf_bias')

        if not data_ok or ema9 is None or ema21 is None or rsi is None:
            return {
                'trend_bias': 'SIDEWAY',
                'action': 'MONITORING',
                'confidence': 40.0,
                'structure': f"{tf} Chờ dữ liệu nến đủ (cần ≥50 nến MT5)",
                'trigger': 'Chưa đủ nến MT5 để phân tích lướt sóng — không vào lệnh.',
                'rationale': 'Bot chỉ phân tích từ nến thật. Terminal chưa trả đủ rates.',
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        bull = (
            ema9 > ema21
            and price >= ema9
            and 48 <= rsi <= 72
            and (macd_h is None or macd_h >= 0)
        )
        bear = (
            ema9 < ema21
            and price <= ema9
            and 28 <= rsi <= 52
            and (macd_h is None or macd_h <= 0)
        )

        if not bull and not bear:
            if (
                bb_lo is not None and price <= bb_lo and rsi < 32
                and ema9 >= ema21 and htf_bias != 'BEARISH'
            ):
                bull = True
            elif (
                bb_up is not None and price >= bb_up and rsi > 68
                and ema9 <= ema21 and htf_bias != 'BULLISH'
            ):
                bear = True

        if htf_bias == 'BEARISH' and bull:
            bull = False
        if htf_bias == 'BULLISH' and bear:
            bear = False

        htf_note = f" HTF={htf_bias or 'n/a'}."

        if bull and not bear:
            conf = 72.0
            if macd_h is not None and macd_h > 0:
                conf += 8
            if 50 <= rsi <= 68:
                conf += 5
            if htf_bias == 'BULLISH':
                conf += 6
            conf = min(92.0, max(55.0, conf))
            t_lo = round(price + atr * 0.6, digits)
            t_hi = round(price + atr * 1.4, digits)
            return {
                'trend_bias': 'BULLISH',
                'action': 'READY_TO_BUY',
                'confidence': round(conf, 1),
                'structure': f"{tf} Scalp BUY: EMA9>EMA21 + MACD≥0 + RSI={rsi}",
                'trigger': (
                    f"LƯỚT SÓNG BUY MARKET tại Ask. Close={round(price, digits)}, "
                    f"EMA9={round(ema9, digits)} > EMA21={round(ema21, digits)}.{htf_note}"
                ),
                'rationale': (
                    f"[SCALP {tf} nến đóng] Uptrend ngắn: close≥EMA9>EMA21, RSI={rsi}, "
                    f"MACD_hist={macd_h}.{htf_note} Không BUY nếu HTF giảm."
                ),
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if bear and not bull:
            conf = 72.0
            if macd_h is not None and macd_h < 0:
                conf += 8
            if 32 <= rsi <= 50:
                conf += 5
            if htf_bias == 'BEARISH':
                conf += 6
            conf = min(92.0, max(55.0, conf))
            t_lo = round(price - atr * 1.4, digits)
            t_hi = round(price - atr * 0.6, digits)
            return {
                'trend_bias': 'BEARISH',
                'action': 'READY_TO_SELL',
                'confidence': round(conf, 1),
                'structure': f"{tf} Scalp SELL: EMA9<EMA21 + MACD≤0 + RSI={rsi}",
                'trigger': (
                    f"LƯỚT SÓNG SELL MARKET tại Bid. Close={round(price, digits)}, "
                    f"EMA9={round(ema9, digits)} < EMA21={round(ema21, digits)}.{htf_note}"
                ),
                'rationale': (
                    f"[SCALP {tf} nến đóng] Downtrend ngắn: close≤EMA9<EMA21, RSI={rsi}, "
                    f"MACD_hist={macd_h}.{htf_note}"
                ),
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if htf_bias == 'BEARISH':
            return {
                'trend_bias': 'BEARISH',
                'action': 'MONITORING',
                'confidence': 58.0,
                'structure': f"{tf} HTF giảm — chặn BUY ngược sóng",
                'trigger': (
                    'Xu hướng khung lớn đang giảm. Không mở BUY; '
                    'chờ EMA9<EMA21 + MACD≤0 để SELL.'
                ),
                'rationale': (
                    f"[SCALP {tf}] HTF BEARISH lọc ngược sóng. "
                    f"EMA9={round(ema9, digits)}, EMA21={round(ema21, digits)}, RSI={rsi}."
                ),
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        if htf_bias == 'BULLISH':
            return {
                'trend_bias': 'BULLISH',
                'action': 'MONITORING',
                'confidence': 58.0,
                'structure': f"{tf} HTF tăng — chặn SELL ngược sóng",
                'trigger': (
                    'Xu hướng khung lớn đang tăng. Không mở SELL; '
                    'chờ EMA9>EMA21 + MACD≥0 để BUY.'
                ),
                'rationale': (
                    f"[SCALP {tf}] HTF BULLISH lọc ngược sóng. "
                    f"EMA9={round(ema9, digits)}, EMA21={round(ema21, digits)}, RSI={rsi}."
                ),
                'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        return {
            'trend_bias': 'SIDEWAY',
            'action': 'MONITORING',
            'confidence': 55.0,
            'structure': f"{tf} Scalp range — chưa có momentum EMA/MACD",
            'trigger': 'Không vào lệnh khi EMA/MACD/RSI chưa đồng thuận.',
            'rationale': (
                f"[SCALP {tf}] Sideway: EMA9={round(ema9, digits) if ema9 else '-'}, "
                f"EMA21={round(ema21, digits) if ema21 else '-'}, RSI={rsi}, MACD={macd_h}.{htf_note}"
            ),
            'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
            'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
        }

    @staticmethod
    def _decide_swing(**kw) -> dict:
        """Dài hạn / trend: EMA50/200 + MACD. Vào khi rõ xu hướng; chờ hồi nếu giá quá xa EMA50."""
        price = kw['price']
        atr = kw['atr']
        rsi = kw['rsi']
        ema50 = kw['ema50']
        ema200 = kw['ema200']
        macd_h = kw['macd_h']
        r1, r2, s1, s2 = kw['r1'], kw['r2'], kw['s1'], kw['s2']
        tf, digits, data_ok = kw['timeframe'], kw['digits'], kw['data_ok']

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
        # Không có EMA200: dùng EMA50 + giá + MACD
        if ema200 is None:
            up_trend = price > ema50 and (macd_h is None or macd_h >= 0) and rsi >= 50
            down_trend = price < ema50 and (macd_h is None or macd_h <= 0) and rsi <= 50

        dist_ema = abs(price - ema50)
        extended = dist_ema > (1.5 * atr)

        if up_trend:
            conf = 72.0
            if macd_h is not None and macd_h > 0:
                conf += 10
            if 45 <= rsi <= 68:
                conf += 6
            conf = min(94.0, conf)
            t_lo = round(price + atr * 1.5, digits)
            t_hi = round(price + atr * 3.0, digits)
            if extended and rsi > 65:
                return {
                    'trend_bias': 'BULLISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 5, 1),
                    'structure': f"{tf} Uptrend EMA50>EMA200 — giá quá xa EMA50 ({round(dist_ema, digits)})",
                    'trigger': (
                        f"Chờ hồi về gần EMA50≈{round(ema50, digits)} rồi BUY. "
                        f"Giữ lệnh dài hạn bằng trail SL khi đã vào."
                    ),
                    'rationale': (
                        f"[SWING {tf} nến MT5] Xu hướng tăng (EMA50={round(ema50, digits)}"
                        f"{f', EMA200={round(ema200, digits)}' if ema200 else ''}), "
                        f"nhưng giá cách EMA50 > 1.5×ATR — không chase. RSI={rsi}."
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            return {
                'trend_bias': 'BULLISH',
                'action': 'READY_TO_BUY',
                'confidence': round(conf, 1),
                'structure': f"{tf} Trend BUY: EMA50>EMA200 + giá trên EMA50",
                'trigger': (
                    f"DÀI HẠN BUY MARKET tại Ask gần EMA50. "
                    f"Gắn SL bảo vệ + bật dời SL (trail) trên ví để giữ sóng."
                ),
                'rationale': (
                    f"[SWING {tf} nến MT5] Uptrend xác nhận: giá {round(price, digits)} > EMA50="
                    f"{round(ema50, digits)}"
                    f"{f' > EMA200={round(ema200, digits)}' if ema200 else ''}, "
                    f"RSI={rsi}, MACD_hist={macd_h}. Mục tiêu {t_lo}-{t_hi}. "
                    f"Quản trị: min TP / max SL / trail SL theo ví."
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
            conf = min(94.0, conf)
            t_lo = round(price - atr * 3.0, digits)
            t_hi = round(price - atr * 1.5, digits)
            if extended and rsi < 35:
                return {
                    'trend_bias': 'BEARISH',
                    'action': 'WAIT_FOR_PULLBACK',
                    'confidence': round(conf - 5, 1),
                    'structure': f"{tf} Downtrend — giá quá xa EMA50, chờ hồi",
                    'trigger': (
                        f"Chờ hồi lên gần EMA50≈{round(ema50, digits)} rồi SELL. "
                        f"Giữ lệnh bằng trail SL."
                    ),
                    'rationale': (
                        f"[SWING {tf} nến MT5] Downtrend nhưng giá cách EMA50 > 1.5×ATR. "
                        f"RSI={rsi}. Không chase đáy."
                    ),
                    'target_zone': f"{t_lo} - {t_hi}",
                    'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
                }
            return {
                'trend_bias': 'BEARISH',
                'action': 'READY_TO_SELL',
                'confidence': round(conf, 1),
                'structure': f"{tf} Trend SELL: EMA50<EMA200 + giá dưới EMA50",
                'trigger': (
                    f"DÀI HẠN SELL MARKET tại Bid gần EMA50. "
                    f"Gắn SL + trail SL trên ví để giữ sóng giảm."
                ),
                'rationale': (
                    f"[SWING {tf} nến MT5] Downtrend: giá {round(price, digits)} < EMA50="
                    f"{round(ema50, digits)}"
                    f"{f' < EMA200={round(ema200, digits)}' if ema200 else ''}, "
                    f"RSI={rsi}, MACD_hist={macd_h}."
                ),
                'target_zone': f"{t_lo} - {t_hi}",
                'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
            }

        return {
            'trend_bias': 'SIDEWAY',
            'action': 'MONITORING',
            'confidence': 52.0,
            'structure': f"{tf} Sideway — EMA50/200 chưa xếp xu hướng",
            'trigger': 'Không mở lệnh dài hạn khi chưa có trend EMA rõ. Bật trail SL khi đã có lệnh thắng.',
            'rationale': (
                f"[SWING {tf}] Sideway từ nến MT5: EMA50={round(ema50, digits) if ema50 else '-'}, "
                f"EMA200={round(ema200, digits) if ema200 else '-'}, RSI={rsi}."
            ),
            'target_zone': f"{round(price - atr, digits)} - {round(price + atr, digits)}",
            'r1': r1, 'r2': r2, 's1': s1, 's2': s2,
        }

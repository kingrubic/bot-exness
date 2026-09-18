"""Kế hoạch lệnh đa khung: cấu trúc thị trường, vùng S/R thật, xác nhận nến đóng.

Bám theo các case bắt buộc của kế hoạch: đồng thuận đa khung, mâu thuẫn khung,
râu nến chưa đóng, thiếu nến, RR thấp và ATR hỏng.
"""
import time

import pytest

from apps.analysis import config as cfg
from apps.analysis.analyzer import (
    TechnicalAnalyzer,
    _entry_level_sets,
    _retest_confirmed,
)
from apps.analysis.market_structure import (
    BEARISH,
    BULLISH,
    SIDEWAYS,
    breakout_state,
    build_zones,
    classify_trend,
    cluster_levels,
    find_swing_points,
    select_trend,
    structure_label,
)

ATR = 4.0
DIGITS = 2


def tf_pack(trend, confidence=90.0, sufficient=True, candles=240, structure=None):
    if structure is None:
        structure = 'HH/HL' if trend == BULLISH else 'LH/LL' if trend == BEARISH else 'RANGE'
    return {
        'trend': trend, 'confidence': confidence, 'sufficient': sufficient,
        'structure': structure, 'candles': candles, 'scores': {}, 'components': [],
        'ema20': None, 'ema50': None, 'ema200': None,
    }


def zone(low, high, touches=3, weight=4.0):
    return {'low': low, 'high': high, 'mid': (low + high) / 2.0,
            'touches': touches, 'weight': weight}


def decide(*, packs, price, zones, breakouts, atr=ATR, momentum=None, entry_tf='M15'):
    return TechnicalAnalyzer._decide_multi_tf(
        entry_tf=entry_tf, packs=packs, price=price, atr=atr, digits=DIGITS,
        zones=zones, breakouts=breakouts,
        momentum=momentum if momentum is not None else {
            'ema9': 10.0, 'ema21': 9.0, 'rsi': 60.0, 'macd_h': 0.5},
    )


def bearish_momentum():
    return {'ema9': 9.0, 'ema21': 10.0, 'rsi': 40.0, 'macd_h': -0.5}


def no_breakout():
    return {'BUY': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None},
            'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}}


# --------------------------------------------------------------------------
# Hàm thuần: swing, vùng giá, breakout theo nến đóng
# --------------------------------------------------------------------------

def test_find_swing_points_detects_fractal_extremes():
    highs = [10, 11, 15, 11, 10, 9, 10, 12, 10, 9]
    lows = [9, 10, 12, 10, 8, 5, 8, 10, 9, 8]
    swing_highs, swing_lows = find_swing_points(highs, lows, strength=2)
    assert [price for _i, price in swing_highs] == [15, 12]
    assert [price for _i, price in swing_lows] == [5]


def test_structure_label_reads_higher_highs_and_higher_lows():
    assert structure_label([(0, 10.0), (5, 12.0)], [(2, 8.0), (7, 9.0)]) == 'HH/HL'
    assert structure_label([(0, 12.0), (5, 10.0)], [(2, 9.0), (7, 8.0)]) == 'LH/LL'
    assert structure_label([(0, 10.0)], [(2, 8.0)]) == 'UNCLEAR'


def test_cluster_levels_groups_nearby_prices_into_one_zone():
    zones = cluster_levels([(100.0, 1.0), (100.4, 1.0), (108.0, 1.0)], tolerance=1.0)
    assert len(zones) == 2
    assert zones[0]['low'] == 100.0 and zones[0]['high'] == 100.4
    assert zones[0]['touches'] == 2
    assert zones[1]['touches'] == 1


def test_build_zones_weights_higher_timeframes_more():
    zones = build_zones({'M15': [100.0], 'H4': [100.2]},
                        cfg.ZONE_TF_WEIGHTS, tolerance=1.0)
    assert len(zones) == 1
    assert zones[0]['weight'] == pytest.approx(
        cfg.ZONE_TF_WEIGHTS['M15'] + cfg.ZONE_TF_WEIGHTS['H4'])


def test_breakout_needs_candle_close_not_just_wick():
    resistance = zone(100.0, 101.0)
    wick = {'open': 99.0, 'high': 102.0, 'low': 98.5, 'close': 100.5}
    closed = {'open': 99.0, 'high': 102.5, 'low': 98.5, 'close': 102.0}
    assert breakout_state(wick, resistance, ATR, 'BUY')['confirmed'] is False
    assert breakout_state(wick, resistance, ATR, 'BUY')['wick_only'] is True
    assert breakout_state(closed, resistance, ATR, 'BUY')['confirmed'] is True


def test_breakout_close_with_weak_body_or_rejection_wick_is_not_confirmed():
    resistance = zone(100.0, 101.0)
    weak = {'open': 101.45, 'high': 104.0, 'low': 99.0, 'close': 101.5}
    state = breakout_state(weak, resistance, ATR, 'BUY')
    assert state['weak_close'] is True
    assert state['quality_ok'] is False
    assert state['confirmed'] is False
    assert 'body_too_small_atr' in state['failure_reasons']
    assert 'excessive_rejection_wick' in state['failure_reasons']


def test_retest_requires_exact_prior_breakout_transition():
    support = zone(100.0, 101.0)
    base = {'open': 100.0, 'high': 101.0, 'low': 99.5}
    valid = [
        {**base, 'close': 100.0},
        {'open': 100.0, 'high': 101.5, 'low': 99.9, 'close': 101.3},
        {'open': 101.3, 'high': 101.7, 'low': 101.2, 'close': 101.5},
        {'open': 101.4, 'high': 101.6, 'low': 101.0, 'close': 101.4},
    ]
    no_transition = [
        {'open': 101.3, 'high': 101.6, 'low': 101.2, 'close': 101.4},
        {'open': 101.4, 'high': 101.7, 'low': 101.2, 'close': 101.5},
        {'open': 101.4, 'high': 101.6, 'low': 101.0, 'close': 101.4},
    ]
    assert _retest_confirmed(valid, [support], 101.4, 1.0, 'BUY') is True
    assert _retest_confirmed(no_transition, [support], 101.4, 1.0, 'BUY') is False


def test_classify_trend_skips_ema200_when_candles_are_short():
    closes = [100.0 + i * 0.5 for i in range(80)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    pack = classify_trend(highs, lows, closes)
    assert pack['sufficient'] is True
    assert pack['trend'] == BULLISH
    assert pack['ema200'] is None  # thiếu nến → bỏ khỏi điểm, không coi là giảm
    assert 0 < pack['confidence'] <= 100


def test_balanced_direction_scores_are_confidently_sideways():
    trend, confidence = select_trend(
        {'bullish': 50.0, 'bearish': 50.0, 'sideways': 0.0}
    )
    assert trend == SIDEWAYS
    assert confidence == 50.0


# --------------------------------------------------------------------------
# CASE 1-10 của kế hoạch
# --------------------------------------------------------------------------

def test_case1_all_timeframes_bullish_with_confirmed_breakout_is_buy_ready():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['setup_status'] == 'BUY_READY'
    assert out['action'] == 'READY_TO_BUY'
    assert out['trade_bias'] == 'BUY'
    assert out['confidence'] >= cfg.MIN_CONFIDENCE
    assert out['waiting_for'] == []
    # SL nằm dưới vùng hỗ trợ có đệm ATR, TP1 là biên gần của vùng kháng cự.
    assert out['stop_loss'] == pytest.approx(4340.0 - cfg.ATR_SL_BUFFER * ATR, abs=0.01)
    assert out['take_profit_1'] == pytest.approx(4370.0, abs=0.01)
    assert out['take_profit_2'] > out['take_profit_1']
    assert out['risk_reward'] >= cfg.MIN_RISK_REWARD


def test_target_two_stays_beyond_target_one_without_further_structure():
    """Giá đã vượt mọi vùng phía trên: TP1 dùng mục tiêu đo ATR, TP2 vẫn phải xa hơn."""
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4348.0, 4348.5)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4348.5},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['resistance'] is None
    assert out['take_profit_1'] == pytest.approx(4350.0 + cfg.MEASURED_MOVE_ATR * ATR, abs=0.01)
    assert out['take_profit_2'] > out['take_profit_1']


def test_case2_all_timeframes_bearish_with_confirmed_breakdown_is_sell_ready():
    out = decide(
        packs={'M15': tf_pack(BEARISH), 'H1': tf_pack(BEARISH), 'H4': tf_pack(BEARISH)},
        price=4355.0,
        zones=[zone(4320.0, 4322.0), zone(4360.0, 4362.0)],
        breakouts={'BUY': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None},
                   'SELL': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4360.0}},
        momentum=bearish_momentum(),
    )
    assert out['setup_status'] == 'SELL_READY'
    assert out['action'] == 'READY_TO_SELL'
    assert out['stop_loss'] == pytest.approx(4362.0 + cfg.ATR_SL_BUFFER * ATR, abs=0.01)
    assert out['take_profit_1'] == pytest.approx(4322.0, abs=0.01)


def test_case3_entry_timeframe_sideways_only_watches():
    out = decide(
        packs={'M15': tf_pack(SIDEWAYS, confidence=60.0, structure='RANGE'),
               'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts=no_breakout(),
    )
    assert out['setup_status'] in ('WATCHING_BUY', 'WAITING')
    assert out['action'] != 'READY_TO_BUY'
    assert out['entry'] is None and out['stop_loss'] is None
    assert any('đóng cửa' in w or 'phá vùng' in w for w in out['waiting_for'])


def test_sideways_h1_with_lh_ll_blocks_buy_even_when_other_scores_are_strong():
    out = decide(
        packs={
            'M15': tf_pack(BULLISH, confidence=100.0),
            'H1': tf_pack(SIDEWAYS, confidence=100.0, structure='LH/LL'),
            'H4': tf_pack(BULLISH, confidence=100.0),
        },
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={
            'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
            'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None},
        },
    )
    assert out['setup_status'] != 'BUY_READY'
    assert any('structure=LH/LL' in reason for reason in out['waiting_for'])


def test_d1_is_informational_and_does_not_change_entry_score():
    base_packs = {
        'M15': tf_pack(BULLISH),
        'H1': tf_pack(BULLISH),
        'H4': tf_pack(BULLISH),
    }
    kwargs = {
        'price': 4350.0,
        'zones': [zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        'breakouts': {
            'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
            'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None},
        },
    }
    without_d1 = decide(packs=base_packs, **kwargs)
    with_bearish_d1 = decide(
        packs={**base_packs, 'D1': tf_pack(BEARISH, confidence=100.0, structure='LH/LL')},
        **kwargs,
    )
    assert with_bearish_d1['confidence'] == without_d1['confidence']
    assert with_bearish_d1['score_breakdown']['macro'] == 0.0
    assert any('chỉ tham khảo' in reason for reason in with_bearish_d1['reasons'])


def test_d1_levels_are_excluded_from_entry_support_resistance_inputs():
    packs = {
        'M15': {'swing_highs': [110.0], 'swing_lows': [90.0]},
        'H1': {'swing_highs': [120.0], 'swing_lows': [80.0]},
        'H4': {'swing_highs': [130.0], 'swing_lows': [70.0]},
        'D1': {'swing_highs': [101.0], 'swing_lows': [99.0]},
    }
    level_sets = _entry_level_sets(packs)
    assert set(level_sets) == {'M15', 'H1', 'H4'}
    assert 101.0 not in sum(level_sets.values(), [])
    assert 99.0 not in sum(level_sets.values(), [])


def test_case4_conflicting_timeframes_lose_confidence_and_never_go_ready():
    aligned = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    conflicted = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BEARISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert conflicted['confidence'] < aligned['confidence']
    assert conflicted['score_breakdown']['conflict_penalty'] < 0
    assert conflicted['setup_status'] not in ('BUY_READY', 'SELL_READY')


def test_case5_wick_through_resistance_without_close_is_not_ready():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': False, 'wick_only': True, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['setup_status'] != 'BUY_READY'
    assert any('râu nến' in w for w in out['waiting_for'])


def test_case6_closed_candle_beyond_resistance_can_be_ready():
    zones = [zone(4340.0, 4342.0), zone(4370.0, 4372.0)]
    candle = {'open': 4343.0, 'high': 4351.0, 'low': 4342.5, 'close': 4350.0}
    state = breakout_state(candle, zones[0], ATR, 'BUY')
    assert state['confirmed'] is True
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0, zones=zones,
        breakouts={'BUY': dict(state, retest=False),
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['setup_status'] == 'BUY_READY'


def test_case7_missing_h4_candles_forces_wait():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH),
               'H4': tf_pack(SIDEWAYS, confidence=0.0, sufficient=False, candles=12)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['setup_status'] == 'WAITING'
    assert out['action'] == 'MONITORING'
    assert out['confidence'] == 0.0
    assert any('H4' in reason for reason in out['reasons'])
    assert out['entry'] is None


def test_case8_risk_reward_below_minimum_blocks_ready():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[
            zone(4300.0, 4302.0),
            zone(4352.0, 4352.2),
            zone(4353.0, 4353.2),
        ],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4349.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['setup_status'] != 'BUY_READY'
    assert any('Risk/Reward' in w for w in out['waiting_for'])
    assert out['entry'] is None


def test_tp2_can_validate_setup_when_tp1_rr_is_below_minimum():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4388.83,
        zones=[zone(4381.36, 4381.36), zone(4402.61, 4402.61)],
        breakouts={
            'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4381.36},
            'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None},
        },
        atr=9.28,
    )
    assert out['risk_reward_1'] < cfg.MIN_RISK_REWARD
    assert out['risk_reward_2'] >= cfg.MIN_RISK_REWARD
    assert out['risk_reward'] == out['risk_reward_2']
    assert out['setup_status'] == 'BUY_READY'


def test_case9_price_sitting_inside_a_single_zone_waits():
    out = decide(
        packs={'M15': tf_pack(SIDEWAYS, confidence=55.0, structure='RANGE'),
               'H1': tf_pack(SIDEWAYS, confidence=55.0, structure='RANGE'),
               'H4': tf_pack(SIDEWAYS, confidence=55.0, structure='RANGE')},
        price=4350.0,
        zones=[zone(4349.5, 4350.5)],
        breakouts=no_breakout(),
    )
    assert out['setup_status'] == 'WAITING'
    assert out['action'] == 'MONITORING'
    assert out['trade_bias'] == 'NEUTRAL'
    assert out['entry'] is None and out['take_profit_1'] is None


def test_case10_invalid_atr_produces_no_trade():
    for bad_atr in (0.0, float('nan')):
        out = decide(
            packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
            price=4350.0, atr=bad_atr,
            zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
            breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                       'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
        )
        assert out['action'] == 'MONITORING'
        assert out['setup_status'] == 'WAITING'
        assert out['entry'] is None
        assert any('ATR' in reason for reason in out['reasons'])


# --------------------------------------------------------------------------
# ATR band tách khỏi S/R cấu trúc + tích hợp end-to-end
# --------------------------------------------------------------------------

def test_atr_bands_are_separate_from_structural_levels():
    out = decide(
        packs={'M15': tf_pack(BULLISH), 'H1': tf_pack(BULLISH), 'H4': tf_pack(BULLISH)},
        price=4350.0,
        zones=[zone(4340.0, 4342.0), zone(4370.0, 4372.0)],
        breakouts={'BUY': {'confirmed': True, 'wick_only': False, 'retest': False, 'level': 4344.0},
                   'SELL': {'confirmed': False, 'wick_only': False, 'retest': False, 'level': None}},
    )
    assert out['atr_upper_band'] == pytest.approx(4354.0)
    assert out['atr_lower_band'] == pytest.approx(4346.0)
    # r1/s1 giữ tên cũ nhưng lấy từ vùng cấu trúc, không còn là giá ± ATR.
    assert out['r1'] == pytest.approx(4370.0)
    assert out['s1'] == pytest.approx(4342.0)
    assert out['r1'] != out['atr_upper_band']
    assert out['s1'] != out['atr_lower_band']


@pytest.mark.django_db
def test_forecast_exposes_independent_timeframes_and_atr_bands():
    """Toàn tuyến: H1/H4 phân tích riêng, không copy trend khung vào lệnh."""
    import json
    from decimal import Decimal
    from unittest.mock import patch

    from apps.symbols.models import SymbolConfig
    from apps.trading.mt5_connector import ExnessMT5Connector

    def series(step_seconds, count=240):
        rows, price = [], 2600.0
        now = int(time.time())
        for i in range(count):
            price += 0.9 if i % 5 else -0.4
            rows.append({
                'time': now - (count - i) * step_seconds,
                'open': price - 0.2, 'high': price + 0.6,
                'low': price - 0.6, 'close': price,
            })
        return rows

    by_tf = {tf: series(seconds) for tf, seconds in
             (('M5', 300), ('M15', 900), ('H1', 3600), ('H4', 14400), ('D1', 86400))}

    ExnessMT5Connector._rates_cache.clear()
    last = by_tf['M5'][-1]['close']
    sym = SymbolConfig.objects.create(
        symbol='XAUUSD', display_name='Gold', category='METALS', digits=2,
        current_price=Decimal(str(round(last, 2))),
        current_bid=Decimal(str(round(last - 0.05, 2))),
        current_ask=Decimal(str(round(last + 0.05, 2))),
        timeframe='M5', strategy='SCALPING_BB', is_active=True,
    )
    with patch('apps.trading.mt5_session.MT5NativeSession.copy_rates',
               side_effect=lambda symbol, timeframe, count: by_tf.get(str(timeframe).upper(), [])):
        forecast = TechnicalAnalyzer.generate_market_analysis(sym)

    ind = json.loads(forecast.indicators_json)
    assert set(('M5', 'M15', 'H1', 'H4')).issubset(ind['timeframes'].keys())
    assert ind['timeframes']['H4']['sufficient'] is True
    assert ind['atr_upper_band'] == pytest.approx(ind['signal_price'] + ind['atr'], abs=0.02)
    assert ind['atr_lower_band'] == pytest.approx(ind['signal_price'] - ind['atr'], abs=0.02)
    assert ind['setup_status'] in dict(forecast.SETUP_STATUS_CHOICES)
    assert forecast.setup_status == ind['setup_status']
    assert isinstance(ind['reasons'], list) and ind['reasons']
    assert ind['multi_tf']['rows'][0]['timeframe'] == 'M5'

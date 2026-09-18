import json
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone
from apps.analysis.analyzer import TechnicalAnalyzer, _closed_rates, _rsi
from apps.plans.entry_checks import entry_preview
from apps.plans.planner import AutoPlanGenerator


def signal(**changes):
    values = dict(price=4308.56, atr=8.41, rsi=50.4, ema9=4313.03,
                  ema21=4309.99, macd_h=-1.74967, r1=4335.38, r2=4334,
                  s1=4274.01, s2=4275, bb_mid=4300, bb_up=4340, bb_lo=4260,
                  timeframe='M15', digits=2, data_ok=True, struct_bias='BULLISH')
    values.update(changes)
    return TechnicalAnalyzer._decide_scalp(**values)


def test_screenshot_waits_instead_of_buying():
    assert signal()['action'] == 'WAIT_FOR_PULLBACK'


@pytest.mark.parametrize('changes', [dict(macd_h=None), dict(macd_h=float('nan')),
                                    dict(atr=0), dict(data_ok=False)])
def test_missing_or_invalid_indicators_never_trade(changes):
    assert signal(**changes)['action'] not in ('READY_TO_BUY', 'READY_TO_SELL')


def test_bullish_structure_cannot_override_negative_momentum():
    assert signal(price=4315, macd_h=-0.01)['action'] == 'WAIT_FOR_PULLBACK'


def test_bearish_structure_cannot_override_positive_momentum():
    assert signal(price=4300, ema9=4305, ema21=4310, rsi=45,
                  macd_h=1, struct_bias='BEARISH')['action'] == 'WAIT_FOR_PULLBACK'


def test_confirmed_buy_and_sell_can_pass():
    assert signal(price=4300, ema9=4299, ema21=4295, rsi=55, macd_h=1)['action'] == 'READY_TO_BUY'
    assert signal(price=4300, ema9=4301, ema21=4305, rsi=45,
                  macd_h=-1, struct_bias='BEARISH')['action'] == 'READY_TO_SELL'


def test_rsi_flat_is_neutral():
    assert _rsi([100] * 30) == 50


def test_closed_candles_exclude_live_bar_and_reject_stale_or_unordered():
    bars = [dict(time=t, open=100, high=101, low=99, close=100) for t in (600, 900, 1200)]
    assert [r['time'] for r in _closed_rates(bars, 'M5', now=1250)] == [600, 900]
    assert _closed_rates(bars, 'M5', now=2400) == []
    assert _closed_rates(bars[::-1], 'M5', now=1250) == []
    assert _closed_rates([dict(open=100, high=101, low=99, close=100)], 'M5', now=1250) == []


def valid_inputs():
    wallet = SimpleNamespace(is_active=True, currency='USD', allowed_symbols=['XAUUSD'],
        min_take_profit_usd=15, max_stop_loss_usd=10, default_lot_size=.01,
        risk_percent=1.5, equity_db=1000, balance_db=1000, floating_pnl=0,
        mt5_login='', max_daily_loss_percent=4, today_pnl=0)
    sym = SimpleNamespace(symbol='XAUUSD', is_active=True, current_bid=4300,
        current_ask=4300.02, contract_size=100, digits=2, point_size=.01,
        max_allowed_spread=3.5)
    fc = SimpleNamespace(recommended_action='READY_TO_BUY', timeframe='M15',
        updated_at=timezone.now(), indicators={'signal_version': 2, 'data_ok': True,
        'closed_candle_time': int(time.time()) - 900, 'atr': 8.41,
        'signal_price': 4300, 'ema9': 4299, 'r1': 4335, 's1': 4270,
        'stop_loss': 4290.02, 'take_profit_1': 4310.02, 'take_profit_2': 4315.02})
    return wallet, sym, fc


def test_preview_uses_ask_and_computes_actual_stop_and_risk():
    wallet, sym, fc = valid_inputs()
    result = entry_preview(wallet, sym, fc)
    assert result['allowed'], result
    assert result['entry_price'] == 4300.02
    assert result['stop_loss'] == 4290.02
    assert result['take_profit_1'] == 4310.02
    assert result['take_profit_2'] == 4315.02
    assert result['target_source'] == 'STRUCTURE_TP2'
    assert result['rr_ratio'] == 1.5
    assert result['risk_amount_usd'] == 10


@pytest.mark.parametrize('field,value', [('max_stop_loss_usd', 5),
    ('risk_percent', .5), ('today_pnl', -35),
    ('default_lot_size', float('nan')), ('equity_db', -1), ('currency', 'USC')])
def test_wallet_risk_rejections(field, value):
    wallet, sym, fc = valid_inputs()
    setattr(wallet, field, value)
    assert not entry_preview(wallet, sym, fc)['allowed']


def test_blank_usd_tp_sl_uses_structural_levels():
    wallet, sym, fc = valid_inputs()
    wallet.min_take_profit_usd = None
    wallet.max_stop_loss_usd = None
    result = entry_preview(wallet, sym, fc)
    assert result['allowed'], result
    assert result['stop_loss'] == fc.indicators['stop_loss']
    assert result['target_price'] == fc.indicators['take_profit_2']
    assert result['stop_source'] == 'STRUCTURE_ATR'


def test_stale_signal_and_spread_and_price_drift_rejected():
    wallet, sym, fc = valid_inputs()
    fc.updated_at -= timedelta(seconds=31)
    assert not entry_preview(wallet, sym, fc)['allowed']
    fc.updated_at = timezone.now()
    sym.current_ask = 4301
    assert not entry_preview(wallet, sym, fc)['allowed']
    sym.current_bid, sym.current_ask = 4310, 4310.02
    assert not entry_preview(wallet, sym, fc)['allowed']


def test_missing_structural_plan_rejected():
    wallet, sym, fc = valid_inputs()
    del fc.indicators['stop_loss']
    result = entry_preview(wallet, sym, fc)
    assert not result['allowed']
    assert 'Thiếu dữ liệu' in result['reason']


@pytest.mark.parametrize('action', ['BUY', 'SELL', '', 'MONITORING', 'WAIT_FOR_PULLBACK', 'UNKNOWN'])
def test_only_explicit_ready_action_allows_direction(action):
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(
        recommended_action=action, trend_bias='BULLISH')) is None


@pytest.mark.django_db
def test_plan_execution_sends_protective_stop_and_rechecks_forecast():
    from apps.accounts.models import WalletAccount
    from apps.symbols.models import SymbolConfig
    from apps.analysis.models import MarketForecast
    from apps.trading.execution_engine import ExecutionEngine
    wallet_data, sym_data, fc_data = valid_inputs()
    wallet = WalletAccount.objects.create(name='Safety', bot_status='RUNNING', account_type='DEMO',
        balance_db=1000, equity_db=1000, min_take_profit_usd=15, max_stop_loss_usd=10,
        max_open_trades=2, allowed_symbols_json='["XAUUSD"]')
    sym = SymbolConfig.objects.create(symbol='XAUUSD', current_bid=4300,
        current_ask=Decimal('4300.02'), contract_size=100, point_size=.01, digits=2)
    fc = MarketForecast.objects.create(symbol='XAUUSD', timeframe='M15',
        recommended_action='READY_TO_BUY', trend_bias='BULLISH', current_price=4300,
        indicators_json=json.dumps(fc_data.indicators))
    plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc)
    assert plan is not None
    assert plan.stop_loss == Decimal('4290.02')
    assert plan.take_profit_1 == Decimal('4310.02')
    assert plan.take_profit_2 == Decimal('4315.02')
    connector = MagicMock()
    connector.connect.return_value = True
    connector.send_order.return_value = dict(success=True, ticket='101', price=4300.02, volume=.01)
    with patch('apps.trading.execution_engine.ExnessMT5Connector', return_value=connector), \
         patch.object(AutoPlanGenerator, 'refresh_symbol_market_price', return_value=sym):
        pos = ExecutionEngine.trigger_plan_to_position(plan)
    assert pos is not None
    assert connector.send_order.call_args.kwargs['sl'] == 4290.02
    assert connector.send_order.call_args.kwargs['tp'] == 4315.02
    assert pos.stop_loss == plan.stop_loss
    assert pos.take_profit == plan.take_profit_2
    plan2 = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc)
    assert plan2 is not None
    fc.recommended_action = 'WAIT_FOR_PULLBACK'
    fc.save()
    connector.send_order.reset_mock()
    with patch('apps.trading.execution_engine.ExnessMT5Connector', return_value=connector), \
         patch.object(AutoPlanGenerator, 'refresh_symbol_market_price', return_value=sym):
        assert ExecutionEngine.trigger_plan_to_position(plan2) is None
    connector.send_order.assert_not_called()


@pytest.mark.django_db
def test_same_closed_candle_can_submit_only_one_order():
    from apps.accounts.models import WalletAccount
    from apps.symbols.models import SymbolConfig
    from apps.analysis.models import MarketForecast
    from apps.plans.models import TradeSignalClaim
    from apps.trading.execution_engine import ExecutionEngine

    _wallet_data, _sym_data, fc_data = valid_inputs()
    wallet = WalletAccount.objects.create(
        name='Idempotent', bot_status='RUNNING', account_type='DEMO',
        balance_db=1000, equity_db=1000, max_open_trades=3,
        min_take_profit_usd=None, max_stop_loss_usd=None,
        allowed_symbols_json='["XAUUSD"]',
    )
    sym = SymbolConfig.objects.create(
        symbol='XAUUSD', current_bid=4300, current_ask=Decimal('4300.02'),
        contract_size=100, point_size=.01, digits=2,
    )
    fc = MarketForecast.objects.create(
        symbol='XAUUSD', timeframe='M15',
        recommended_action='READY_TO_BUY', trend_bias='BULLISH',
        current_price=4300, indicators_json=json.dumps(fc_data.indicators),
    )
    connector = MagicMock()
    connector.connect.return_value = True
    connector.send_order.return_value = {
        'success': True, 'ticket': 'IDEMPOTENT-1',
        'price': 4300.02, 'volume': .01,
    }

    with patch('apps.trading.execution_engine.ExnessMT5Connector', return_value=connector), \
         patch.object(AutoPlanGenerator, 'refresh_symbol_market_price', return_value=sym):
        first_plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc)
        assert ExecutionEngine.trigger_plan_to_position(first_plan) is not None

        second_plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc)
        assert second_plan is not None
        assert ExecutionEngine.trigger_plan_to_position(second_plan) is None

    assert connector.send_order.call_count == 1
    claim = TradeSignalClaim.objects.get(wallet=wallet, symbol='XAUUSD')
    assert claim.candle_time == fc_data.indicators['closed_candle_time']
    assert claim.status == 'EXECUTED'
    assert claim.order_ticket == 'IDEMPOTENT-1'


def test_invalid_broker_stops_never_retry_without_stop():
    from apps.trading import mt5_session as module
    mt5 = MagicMock()
    mt5.symbol_info_tick.return_value = SimpleNamespace(ask=100, bid=99.99)
    mt5.order_send.return_value = SimpleNamespace(retcode=module.RET_INVALID_STOPS, comment='Invalid stops')
    with patch.object(module, 'mt5', mt5), \
         patch.object(module.MT5NativeSession, 'resolve_symbol', return_value='XAUUSD'), \
         patch.object(module.MT5NativeSession, 'spec', return_value={'filling_modes': [0]}):
        result = module.MT5NativeSession.send_market('XAUUSD', 'BUY', .01, sl=95)
    assert not result['success']
    assert mt5.order_send.call_count == 1
    assert mt5.order_send.call_args.args[0]['sl'] == 95


@pytest.mark.django_db
def test_daily_budget_includes_open_position_risk():
    from apps.accounts.models import WalletAccount
    from apps.symbols.models import SymbolConfig
    from apps.trading.models import Position
    wallet_data, sym_data, forecast = valid_inputs()
    wallet = WalletAccount.objects.create(name='Exposure', balance_db=1000, equity_db=1000,
        min_take_profit_usd=15, max_stop_loss_usd=10, max_daily_loss_percent=1.5,
        allowed_symbols_json='["XAUUSD"]')
    sym = SymbolConfig.objects.create(symbol='XAUUSD', current_bid=4300,
        current_ask=Decimal('4300.02'), contract_size=100, point_size=.01, digits=2)
    assert entry_preview(wallet, sym, forecast)['allowed']
    pos = Position.objects.create(wallet=wallet, ticket='LOCAL-RISK', symbol='XAUUSD',
        position_type='BUY', lot_size=.01, open_price=4300, current_price=4300,
        stop_loss=4290, opened_at=timezone.now())
    result = entry_preview(wallet, sym, forecast)
    assert not result['allowed']
    assert 'ngân sách' in result['reason']
    pos.stop_loss = None
    pos.save()
    assert 'chưa xác định' in entry_preview(wallet, sym, forecast)['reason']


@pytest.mark.django_db
def test_rejected_plan_stops_entry_loop():
    from apps.accounts.models import WalletAccount
    from apps.symbols.models import SymbolConfig
    from apps.analysis.models import MarketForecast
    from apps.trading.execution_engine import ExecutionEngine
    wallet = WalletAccount.objects.create(name='Blocked', balance_db=1000,
        bot_status='RUNNING', allowed_symbols_json='["XAUUSD"]')
    sym = SymbolConfig.objects.create(symbol='XAUUSD')
    forecast = MarketForecast.objects.create(symbol='XAUUSD', current_price=4300,
        recommended_action='READY_TO_BUY')
    with patch.object(AutoPlanGenerator, 'generate_plan_for_wallet', return_value=None) as generate:
        ExecutionEngine.try_immediate_market_entries({'XAUUSD': (sym, forecast)})
    generate.assert_called_once()

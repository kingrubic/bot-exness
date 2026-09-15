import pytest
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import WalletAccount
from apps.symbols.models import SymbolConfig
from apps.analysis.models import MarketForecast
from apps.plans.models import TradingPlan
from apps.plans.planner import AutoPlanGenerator
from apps.trading.models import Position, TradeHistory
from apps.trading.execution_engine import ExecutionEngine

@pytest.mark.django_db
def test_instant_scalping_plan_generation():
    wallet = WalletAccount.objects.create(
        name="Test Scalp Wallet",
        account_type="DEMO",
        mt5_login="11223344",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        risk_percent=1.5,
        allowed_symbols_json='["XAUUSD"]',
        is_active=True,
        bot_status='RUNNING'
    )
    
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Vàng Đô La",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2750.50"),
        current_bid=Decimal("2750.35"),
        current_ask=Decimal("2750.65"),
        atr_value=8.5,
        is_active=True
    )
    
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD",
        timeframe="M5",
        trend_bias="BULLISH",
        confidence_score=88.5,
        current_price=Decimal("2750.50")
    )
    
    plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast)
    assert plan is not None
    assert plan.direction == 'BUY'
    # Instant market entry at ask price
    assert float(plan.entry_price) == 2750.65
    assert plan.calculated_lot >= 0.01

@pytest.mark.django_db
def test_close_position_simulation():
    wallet = WalletAccount.objects.create(
        name="Test Sim Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        is_active=True
    )
    
    pos = Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-12345678",
        symbol="XAUUSD",
        position_type="BUY",
        lot_size=0.02,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2755.00"),
        floating_pnl=Decimal("10.00"),
        opened_at=timezone.now()
    )
    
    ok, msg = ExecutionEngine.close_position(pos, reason="MANUAL_CLOSE")
    assert ok is True
    assert not Position.objects.filter(ticket="LOCAL-12345678").exists()
    assert TradeHistory.objects.filter(ticket="LOCAL-12345678").exists()
    assert float(wallet.balance) == 1010.00


@pytest.mark.django_db
def test_trend_pyramiding_and_anti_burn_lot_sizing():
    wallet = WalletAccount.objects.create(
        name="Scalp Anti-Burn Wallet",
        account_type="DEMO",
        mt5_login="99887766",
        balance_db=Decimal("5000.00"),
        capital=Decimal("5000.00"),
        margin_free=Decimal("4500.00"),
        default_lot_size=0.05,
        leverage=500,
        risk_percent=2.0,
        allowed_symbols_json='["XAUUSD"]',
        is_active=True,
        bot_status='RUNNING',
        max_open_trades=2
    )

    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold Spot",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.90"),
        current_ask=Decimal("2750.10"),
        atr_value=5.0,
        is_active=True
    )

    forecast = MarketForecast.objects.create(
        symbol="XAUUSD",
        timeframe="M5",
        trend_bias="BULLISH",
        confidence_score=90.0,
        current_price=Decimal("2750.00")
    )

    # 1. Lệnh 1 ban đầu
    plan1 = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast, is_pyramiding=False)
    assert plan1 is not None
    assert plan1.calculated_lot >= 0.01
    assert "Lướt" in plan1.rationale

    # 2. Lệnh 2 nhồi theo Trend (Pyramiding)
    plan2 = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast, is_pyramiding=True)
    assert plan2 is not None
    assert "Lướt" in plan2.rationale
    assert plan2.calculated_lot >= 0.01


@pytest.mark.django_db
def test_dynamic_plan_and_trailing_sltp_updates():
    wallet = WalletAccount.objects.create(
        name="Trailing Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("3000.00"),
        capital=Decimal("3000.00"),
        is_active=True,
        allowed_symbols_json='["EURUSD"]'
    )

    sym = SymbolConfig.objects.create(
        symbol="EURUSD",
        display_name="Euro / US Dollar",
        category="FOREX",
        digits=5,
        point_size=0.00001,
        contract_size=100000.0,
        current_price=Decimal("1.08500"),
        current_bid=Decimal("1.08495"),
        current_ask=Decimal("1.08505"),
        atr_value=0.0015,
        is_active=True
    )

    forecast = MarketForecast.objects.create(
        symbol="EURUSD",
        timeframe="M5",
        trend_bias="BULLISH",
        confidence_score=92.0,
        current_price=Decimal("1.08500")
    )

    # 1. Kiểm tra cập nhật kế hoạch liên tục theo giá mới
    p1 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast)
    assert p1.status == 'PENDING_TRIGGER'
    assert float(p1.entry_price) == 1.08505

    # Giả lập giá thị trường tăng lên 1.08600
    sym.current_price = Decimal("1.08600")
    sym.current_ask = Decimal("1.08605")
    sym.save()

    p2 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast)
    assert p2.id == p1.id  # Giữ nguyên ID, cập nhật nội dung
    assert float(p2.entry_price) == 1.08605

    # 2. Kiểm tra tự động chốt lời khi có lãi ròng
    pos = Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-8888",
        symbol="EURUSD",
        position_type="BUY",
        lot_size=0.05,
        open_price=Decimal("1.08500"),
        current_price=Decimal("1.08500"),
        stop_loss=None,
        take_profit=None,
        opened_at=timezone.now()
    )

    # Giá tăng lên 1.08550 (+50 points = 5.0 pips tiêu chuẩn -> có lãi)
    sym.current_price = Decimal("1.08550")
    sym.save()

    ExecutionEngine.update_positions_and_pnl()

    # Vị thế có lãi được tự động chốt lời và chuyển sang TradeHistory
    assert not Position.objects.filter(ticket="LOCAL-8888").exists()
    assert TradeHistory.objects.filter(ticket="LOCAL-8888").exists()


@pytest.mark.django_db
def test_fee_aware_profit_closing_and_breakeven():
    wallet = WalletAccount.objects.create(
        name="Fee Aware Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True
    )

    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold Spot",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"),
        current_ask=Decimal("2750.20"),
        current_spread_pips=4.0,
        atr_value=4.0,
        is_active=True
    )

    # Vị thế có phí hoa hồng commission = -$0.70
    pos = Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-FEE-1",
        symbol="XAUUSD",
        position_type="BUY",
        lot_size=0.10,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"),
        stop_loss=Decimal("2748.00"),
        take_profit=Decimal("2755.00"),
        commission=Decimal("-0.70"),
        swap=Decimal("0.00"),
        opened_at=timezone.now()
    )

    # Đóng lệnh thủ công hoặc qua close_position: Net PnL = Gross PnL + Commission
    pos.floating_pnl = Decimal("5.00")
    ok, msg = ExecutionEngine.close_position(pos, reason='TP_HIT')
    assert ok is True

    hist = TradeHistory.objects.filter(ticket="LOCAL-FEE-1").first()
    assert hist is not None
    # Net PnL phải là 5.00 - 0.70 = 4.30 USD
    assert float(hist.pnl) == 4.30
    assert float(hist.commission) == -0.70
    assert hist.is_win is True


@pytest.mark.django_db
def test_risk_percent_holds_losing_trade():
    """Lệnh lỗ được gồng, không cắt lỗ tự động."""
    wallet = WalletAccount.objects.create(
        name="SL Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        is_active=True,
        risk_percent=1.5,
        max_daily_loss_percent=4.0,
        max_open_trades=5,
        allowed_symbols_json='["XAUUSD"]'
    )

    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold Spot",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2750.00"),
        is_active=True
    )

    Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-SL-1",
        symbol="XAUUSD",
        position_type="BUY",
        lot_size=0.01,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2749.90"),
        opened_at=timezone.now()
    )

    # Lỗ rất nhỏ chưa tới min_tp $1 → giữ
    wallet.min_take_profit_usd = Decimal("5.00")
    wallet.save(update_fields=['min_take_profit_usd'])
    sym.current_price = Decimal("2749.80")
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    assert Position.objects.filter(ticket="LOCAL-SL-1").exists()

    # Lỗ -$6: gồng, không cắt lỗ tự động
    sym.current_price = Decimal("2744.00")
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    assert Position.objects.filter(ticket="LOCAL-SL-1").exists()
    assert not TradeHistory.objects.filter(ticket="LOCAL-SL-1").exists()


@pytest.mark.django_db
def test_scalp_stop_matches_min_take_profit():
    from apps.trading.execution_engine import ExecutionEngine
    wallet = WalletAccount.objects.create(
        name="Scalp SL",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        min_take_profit_usd=Decimal("1.00"),
        risk_percent=1.5,
    )
    assert ExecutionEngine.wallet_scalp_loss_usd(wallet) == 1.0
    wallet.min_take_profit_usd = Decimal("20.00")
    assert ExecutionEngine.wallet_scalp_loss_usd(wallet) == 15.0


@pytest.mark.django_db
def test_db_stop_loss_price_does_not_close_loser():
    """Giá chạm stop_loss trên DB không tự đóng — gồng đến khi lãi."""
    wallet = WalletAccount.objects.create(
        name="Price SL Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        is_active=True,
        risk_percent=50.0,
        allowed_symbols_json='["XAUUSD"]'
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2747.50"), is_active=True
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-SL-PX", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01,
        open_price=Decimal("2750.00"), current_price=Decimal("2747.50"),
        stop_loss=Decimal("2748.00"), opened_at=timezone.now()
    )
    ExecutionEngine.update_positions_and_pnl()
    assert Position.objects.filter(ticket="LOCAL-SL-PX").exists()
    assert not TradeHistory.objects.filter(ticket="LOCAL-SL-PX").exists()


@pytest.mark.django_db
def test_wallet_max_open_trades_cap():
    """Số lệnh đồng thời lấy từ cấu hình ví, không giới hạn theo vốn."""
    wallet = WalletAccount.objects.create(
        name="Cap Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("5000.00"),
        capital=Decimal("5000.00"),
        margin_free=Decimal("4500.00"),
        is_active=True,
        max_open_trades=2,
        allowed_symbols_json='["XAUUSD"]'
    )
    assert ExecutionEngine.get_max_allowed_positions_for_wallet(wallet) == 2

    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CAP-1", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now()
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CAP-2", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2751.00"),
        current_price=Decimal("2751.00"), opened_at=timezone.now()
    )

    can_enter, _, reason = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can_enter is False
    assert "2/2" in reason

    wallet.max_open_trades = 4
    wallet.save()
    can_enter2, is_pyr, _ = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can_enter2 is True
    assert is_pyr is True


@pytest.mark.django_db
def test_min_take_profit_usd_per_wallet():
    """Chỉ đóng khi lãi ròng đạt min_take_profit_usd của ví."""
    wallet = WalletAccount.objects.create(
        name="Min TP Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        min_take_profit_usd=Decimal("5.00"),
        allowed_symbols_json='["XAUUSD"]'
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2750.50"),
        is_active=True
    )
    Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-MINTP-1",
        symbol="XAUUSD",
        position_type="BUY",
        lot_size=0.01,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2750.50"),
        opened_at=timezone.now()
    )
    ExecutionEngine.update_positions_and_pnl()
    assert Position.objects.filter(ticket="LOCAL-MINTP-1").exists()

    sym.current_price = Decimal("2756.00")
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="LOCAL-MINTP-1").exists()
    hist = TradeHistory.objects.filter(ticket="LOCAL-MINTP-1").first()
    assert hist is not None
    assert float(hist.pnl) > 0


@pytest.mark.django_db
def test_plan_uses_live_market_ask_bid():
    wallet = WalletAccount.objects.create(
        name="Mkt Wallet",
        account_type="DEMO",
        mt5_login="",
        allowed_symbols_json='["XAUUSD"]',
        is_active=True
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold",
        category="METALS",
        digits=2,
        current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"),
        current_ask=Decimal("2750.20"),
        is_active=True
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00")
    )
    buy_plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast)
    assert float(buy_plan.entry_price) == 2750.20

    forecast.trend_bias = "BEARISH"
    forecast.save()
    sell_plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast)
    assert float(sell_plan.entry_price) == 2749.80


@pytest.mark.django_db
def test_purge_failed_and_stale_unfilled_plans():
    wallet = WalletAccount.objects.create(
        name="Purge Wallet",
        account_type="DEMO",
        mt5_login="",
        allowed_symbols_json='["XAUUSD"]',
        is_active=True
    )
    failed = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="fail",
        status="FAILED"
    )
    cancelled = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="SELL",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="cancel",
        status="CANCELLED"
    )
    stale = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="stale",
        status="PENDING_TRIGGER"
    )
    TradingPlan.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(minutes=10))
    fresh = TradingPlan.objects.create(
        wallet=wallet, symbol="EURUSD", direction="BUY",
        entry_price=Decimal("1.08"), entry_zone_low=Decimal("1.08"),
        entry_zone_high=Decimal("1.08"), rationale="fresh",
        status="PENDING_TRIGGER"
    )
    executing = TradingPlan.objects.create(
        wallet=wallet, symbol="GBPUSD", direction="BUY",
        entry_price=Decimal("1.26"), entry_zone_low=Decimal("1.26"),
        entry_zone_high=Decimal("1.26"), rationale="exec",
        status="EXECUTING"
    )

    n = AutoPlanGenerator.purge_dead_plans()
    assert n >= 3
    assert not TradingPlan.objects.filter(pk=failed.pk).exists()
    assert not TradingPlan.objects.filter(pk=cancelled.pk).exists()
    assert not TradingPlan.objects.filter(pk=stale.pk).exists()
    assert TradingPlan.objects.filter(pk=fresh.pk).exists()
    assert TradingPlan.objects.filter(pk=executing.pk).exists()


def test_normalize_mt5_symbol_strips_exness_suffix():
    from apps.trading.mt5_connector import ExnessMT5Connector
    assert ExnessMT5Connector.normalize_symbol('XAUUSDm') == 'XAUUSD'
    assert ExnessMT5Connector.normalize_symbol('BTCUSDm') == 'BTCUSD'
    assert ExnessMT5Connector.normalize_symbol('ETHUSDm') == 'ETHUSD'
    assert ExnessMT5Connector.normalize_symbol('EURUSD') == 'EURUSD'
    assert ExnessMT5Connector.normalize_symbol('US30m') == 'US30'
    cands = ExnessMT5Connector.symbol_candidates('XAUUSD')
    assert 'XAUUSD' in cands
    assert 'XAUUSDm' in cands


@pytest.mark.django_db
def test_default_active_symbols_are_gold_btc_eth():
    from apps.core.trading_defaults import apply_default_active_symbols, DEFAULT_ACTIVE_SYMBOLS
    for name in ('XAUUSD', 'BTCUSD', 'ETHUSD', 'EURUSD'):
        SymbolConfig.objects.create(
            symbol=name, display_name=name, category='FOREX',
            digits=5, point_size=0.00001, is_active=True,
        )
    wallet = WalletAccount.objects.create(
        name="Default Pair Wallet",
        account_type="DEMO",
        mt5_login="998877",
        allowed_symbols_json='["XAUUSD"]',
    )
    apply_default_active_symbols()
    active = set(SymbolConfig.objects.filter(is_active=True).values_list('symbol', flat=True))
    assert active == set(DEFAULT_ACTIVE_SYMBOLS)
    wallet.refresh_from_db()
    assert set(wallet.allowed_symbols) == {'XAUUSD', 'BTCUSD', 'ETHUSD'}


@pytest.mark.django_db
def test_order_source_tag_prefers_saved_user_over_magic():
    from apps.trading.order_source import classify_order_source, remember_order_source
    remember_order_source('12345', 'USER', 0)
    assert classify_order_source(magic=8882026, comment='AI-XAUUSD', ticket='12345') == 'USER'
    remember_order_source('999', 'BOT', 8882026)
    assert classify_order_source(magic=0, comment='', ticket='999') == 'BOT'
    assert classify_order_source(magic=0, comment='manual', ticket='unknown') == 'USER'


def test_mt5_session_helpers_without_terminal():
    from apps.trading.mt5_session import (
        MT5NativeSession, SUCCESS_RETCODES, RET_DONE, RET_INVALID_FILL, RETRY_RETCODES,
    )
    assert MT5NativeSession.normalize_symbol('XAUUSDm') == 'XAUUSD'
    assert 'XAUUSDm' in MT5NativeSession.symbol_candidates('XAUUSD')
    assert RET_DONE in SUCCESS_RETCODES
    assert RET_INVALID_FILL not in SUCCESS_RETCODES
    assert 10004 in RETRY_RETCODES
    first = MT5NativeSession.history_from_for_wallet(1)
    MT5NativeSession.mark_history_synced(1)
    nxt = MT5NativeSession.history_from_for_wallet(1)
    assert nxt > first
    MT5NativeSession._history_cursor.pop(1, None)


def test_mt5_algo_off_status_code():
    from apps.trading.mt5_launcher import MT5Launcher
    assert MT5Launcher.status_code_from_flags(True, True, True, False) == 'algo_off'
    assert MT5Launcher.status_code_from_flags(True, True, True, True) == 'ready'
    assert MT5Launcher.status_code_from_flags(True, True, False, False) == 'not_logged_in'





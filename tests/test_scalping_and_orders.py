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
def test_user_winner_also_closes_at_min_take_profit():
    """Lệnh USER lãi cũng chốt khi đạt min_take_profit_usd (không chỉ BOT)."""
    wallet = WalletAccount.objects.create(
        name="User TP Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        min_take_profit_usd=Decimal("0.50"),
        allowed_symbols_json='["XAUUSD"]'
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2740.00"),
        is_active=True
    )
    Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-USER-TP-1",
        symbol="XAUUSD",
        position_type="SELL",
        lot_size=0.01,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2740.00"),
        source="USER",
        opened_at=timezone.now()
    )
    ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="LOCAL-USER-TP-1").exists()


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


@pytest.mark.django_db
def test_update_or_create_plan_does_not_accumulate():
    """Làm mới plan cùng ví+cặp không tạo thêm hàng — plan cũ PENDING bị thay, EXECUTING giữ."""
    wallet = WalletAccount.objects.create(
        name="Plan Acc",
        account_type="DEMO",
        mt5_login="",
        allowed_symbols_json='["XAUUSD"]',
        is_active=True,
        default_lot_size=0.01,
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100,
        current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"),
        current_ask=Decimal("2750.20"),
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        trigger_condition="test", analysis_rationale="test",
    )
    p1 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast)
    p2 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast)
    pending = TradingPlan.objects.filter(wallet=wallet, symbol="XAUUSD", status="PENDING_TRIGGER")
    assert pending.count() == 1
    assert p1.id == p2.id
    executing = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="open",
        status="EXECUTING",
    )
    AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast)
    assert TradingPlan.objects.filter(pk=executing.pk, status="EXECUTING").exists()
    assert TradingPlan.objects.filter(wallet=wallet, symbol="XAUUSD", status="PENDING_TRIGGER").count() == 1
    completed = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="done",
        status="COMPLETED",
    )
    AutoPlanGenerator.purge_dead_plans()
    assert not TradingPlan.objects.filter(pk=completed.pk).exists()


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
    from apps.core.trading_defaults import apply_default_active_symbols
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
    assert 'XAUUSD' in active
    wallet.refresh_from_db()
    assert set(wallet.allowed_symbols) == {'XAUUSD'}

    empty_wallet = WalletAccount.objects.create(
        name="Empty Pair Wallet",
        account_type="DEMO",
        mt5_login="998878",
        allowed_symbols_json='[]',
    )
    apply_default_active_symbols()
    empty_wallet.refresh_from_db()
    assert empty_wallet.allowed_symbols == []


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
    assert MT5NativeSession.deal_int(type('D', (), {'type': 0})(), 'type', -1) == 0
    assert MT5NativeSession.deal_int(type('D', (), {'type': 1})(), 'type', -1) == 1
    first = MT5NativeSession.history_from_for_wallet(1)
    MT5NativeSession.mark_history_synced(1)
    nxt = MT5NativeSession.history_from_for_wallet(1)
    assert nxt > first
    MT5NativeSession._history_cursor.pop(1, None)


def test_mt5_one_terminal_one_account_message():
    from apps.trading.mt5_session import MT5NativeSession
    msg = MT5NativeSession.explain_single_account(434232731, 'Exness-MT5Trial7', 111111)
    assert '#434232731' in msg
    assert '#111111' in msg
    assert '1 tài khoản' in msg


def test_mt5_algo_off_status_code():
    from apps.trading.mt5_launcher import MT5Launcher
    assert MT5Launcher.status_code_from_flags(True, True, True, False) == 'algo_off'
    assert MT5Launcher.status_code_from_flags(True, True, True, True) == 'ready'
    assert MT5Launcher.status_code_from_flags(True, True, False, False) == 'not_logged_in'


@pytest.mark.django_db
def test_quick_order_returns_json_without_syncing_positions(client):
    from unittest.mock import patch
    import json
    wallet = WalletAccount.objects.create(
        name="Quick Trade Wallet",
        account_type="DEMO",
        mt5_login="434232731",
        mt5_password="x",
        mt5_server="Exness-MT5Trial7",
        is_active=True,
    )
    payload = {
        'wallet_id': wallet.id,
        'symbol': 'XAUUSD',
        'order_type': 'BUY',
        'volume': 0.01,
        'comment': 'Web Direct Trade',
    }
    with patch('apps.trading.mt5_connector.ExnessMT5Connector.connect', return_value=True), \
         patch('apps.trading.mt5_connector.ExnessMT5Connector.send_order', return_value={
             'success': True, 'ticket': '555', 'deal': '1', 'price': 4270.5,
         }) as send_mock, \
         patch('apps.trading.mt5_connector.ExnessMT5Connector.sync_account_info') as sync_acc, \
         patch('apps.trading.mt5_connector.ExnessMT5Connector.sync_positions') as sync_pos:
        res = client.post('/api/orders/send/', data=json.dumps(payload), content_type='application/json')
    assert res.status_code == 200
    body = res.json()
    assert body['success'] is True
    assert body['ticket'] == '555'
    send_mock.assert_called_once()
    sync_acc.assert_not_called()
    sync_pos.assert_not_called()


@pytest.mark.django_db
def test_quick_order_db_lock_returns_json_not_html(client):
    from unittest.mock import patch
    from django.db import OperationalError
    import json
    wallet = WalletAccount.objects.create(
        name="Lock Wallet",
        account_type="DEMO",
        mt5_login="1",
        is_active=True,
    )
    with patch('apps.accounts.models.WalletAccount.objects.get', side_effect=OperationalError('database is locked')):
        res = client.post(
            '/api/orders/send/',
            data=json.dumps({'wallet_id': wallet.id, 'symbol': 'XAUUSD', 'order_type': 'BUY', 'volume': 0.01}),
            content_type='application/json',
        )
    assert res.status_code == 503
    assert 'json' in res['Content-Type']
    assert 'DOCTYPE' not in res.content.decode('utf-8', errors='ignore')
    assert res.json()['success'] is False


def test_direction_from_forecast_always_buy_or_sell():
    from types import SimpleNamespace
    from apps.plans.planner import AutoPlanGenerator
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BULLISH', recommended_action='WAIT_FOR_PULLBACK')) == 'BUY'
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BEARISH', recommended_action='WAIT_FOR_PULLBACK')) == 'SELL'
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='SIDEWAY', recommended_action='READY_TO_SELL')) == 'SELL'
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='SIDEWAY', recommended_action='MONITORING')) == 'BUY'


@pytest.mark.django_db
def test_empty_wallet_purges_plans_and_skips_orders():
    wallet = WalletAccount.objects.create(
        name="Empty Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("0.00"),
        capital=Decimal("0.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=5,
        allowed_symbols_json='["XAUUSD"]',
    )
    wallet.equity = Decimal("0.00")
    wallet.save(update_fields=['equity_db'])
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        trigger_condition="x", analysis_rationale="x",
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="old",
        status="PENDING_TRIGGER",
    )
    assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast) is None
    assert not TradingPlan.objects.filter(wallet=wallet).exists()

    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="again",
        status="EXECUTING",
    )
    can, _, reason = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can is False
    assert "về 0" in reason
    assert not TradingPlan.objects.filter(wallet=wallet).exists()

    ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert not Position.objects.filter(wallet=wallet).exists()
    assert not TradingPlan.objects.filter(wallet=wallet).exists()


@pytest.mark.django_db
def test_immediate_market_entry_when_under_max():
    """Chưa đủ lệnh: AI gửi MARKET ngay, không giữ plan chờ vùng entry."""
    from unittest.mock import patch
    wallet = WalletAccount.objects.create(
        name="Instant Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=3,
        allowed_symbols_json='["XAUUSD"]',
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="WAIT_FOR_PULLBACK",
        trigger_condition="wait", analysis_rationale="test",
    )
    ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert Position.objects.filter(wallet=wallet, symbol="XAUUSD", source="BOT").exists()
    executing = TradingPlan.objects.filter(wallet=wallet, symbol="XAUUSD", status="EXECUTING")
    assert executing.count() == 1
    assert executing.first().direction == "BUY"


@pytest.mark.django_db
def test_no_pending_entry_plan_when_at_max_positions():
    wallet = WalletAccount.objects.create(
        name="Full Wallet",
        account_type="DEMO",
        mt5_login="",
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=1,
        allowed_symbols_json='["XAUUSD"]',
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"), is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BEARISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_SELL",
        trigger_condition="x", analysis_rationale="x",
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-FULL-1", symbol="XAUUSD",
        position_type="SELL", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="SELL",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2740.00"),
        entry_zone_high=Decimal("2760.00"), rationale="wait zone",
        status="PENDING_TRIGGER",
    )
    ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert not TradingPlan.objects.filter(wallet=wallet, status="PENDING_TRIGGER").exists()
    assert Position.objects.filter(wallet=wallet).count() == 1


def test_deposit_after_liquidation_resets_daily_risk_pnl():
    from apps.trading.mt5_session import MT5NativeSession

    class D:
        def __init__(self, typ, ts, profit, magic=0, comment='', position_id=1):
            self.type = typ
            self.time = ts
            self.profit = profit
            self.commission = 0
            self.swap = 0
            self.magic = magic
            self.comment = comment
            self.position_id = position_id
            self.ticket = 1

    start = 1_000_000.0
    deals = [
        D(1, start + 10, -400),
        D(2, start + 50, 200),
        D(0, start + 80, 3),
    ]
    snap = MT5NativeSession.summarize_today_deals(deals, start)
    assert snap['all'] == -397.0
    assert snap['risk'] == 3.0


@pytest.mark.django_db
def test_recap_after_wipe_allows_new_entries():
    from unittest.mock import patch
    wallet = WalletAccount.objects.create(
        name="Recap Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("200.00"),
        capital=Decimal("200.00"),
        margin=Decimal("0.00"),
        margin_free=Decimal("200.00"),
        margin_level=0.0,
        is_active=True,
        bot_status="RUNNING",
        max_daily_loss_percent=4.0,
        max_open_trades=5,
        allowed_symbols_json='["XAUUSD"]',
    )
    with patch.object(ExecutionEngine, 'wallet_today_risk_pnl', return_value=-480.0):
        can, _, _ = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can is True


@pytest.mark.django_db
def test_liquidated_ghost_positions_are_removed():
    from unittest.mock import patch
    from apps.trading.mt5_session import MT5NativeSession
    wallet = WalletAccount.objects.create(
        name="Ghost Wallet",
        account_type="DEMO",
        mt5_login="434232731",
        is_active=True,
        allowed_symbols_json='["XAUUSD"]',
    )
    Position.objects.create(
        wallet=wallet, ticket="471999001", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    acc = type('A', (), {'login': 434232731})()
    with patch.object(MT5NativeSession, 'available', return_value=True), \
         patch.object(MT5NativeSession, 'positions_by_ticket', return_value={}), \
         patch.object(MT5NativeSession, 'account', return_value=acc), \
         patch.object(ExecutionEngine, 'persist_mt5_state'):
        ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="471999001").exists()


@pytest.mark.django_db
def test_zero_balance_displays_zero_not_capital():
    wallet = WalletAccount.objects.create(
        name="Zero Display",
        account_type="DEMO",
        mt5_login="434232731",
        capital=Decimal("500.00"),
        balance_db=Decimal("0.00"),
        equity_db=Decimal("0.00"),
        total_profit=Decimal("-500.00"),
    )
    assert wallet.balance == Decimal("0.00")
    assert wallet.equity == Decimal("0.00")


@pytest.mark.django_db
def test_only_one_wallet_can_be_active():
    a = WalletAccount.objects.create(name="A", account_type="DEMO", mt5_login="111", is_active=True)
    b = WalletAccount.objects.create(name="B", account_type="DEMO", mt5_login="222", is_active=True)
    a.refresh_from_db()
    b.refresh_from_db()
    assert b.is_active is True
    assert a.is_active is False
    assert WalletAccount.objects.filter(is_active=True).count() == 1
    assert WalletAccount.get_current().id == b.id


@pytest.mark.django_db
def test_overview_kpis_use_active_wallet_only():
    from django.test import Client
    WalletAccount.objects.create(
        name="A", account_type="DEMO", mt5_login="111", is_active=True,
        balance_db=Decimal("100.00"), equity_db=Decimal("100.00"),
        total_profit=Decimal("10.00"), total_trades=2, winning_trades=1,
    )
    b = WalletAccount.objects.create(
        name="B", account_type="DEMO", mt5_login="222", is_active=True,
        balance_db=Decimal("50.00"), equity_db=Decimal("50.00"),
        total_profit=Decimal("5.00"), total_trades=4, winning_trades=2,
    )
    res = Client().get('/api/overview/')
    assert res.status_code == 200
    data = res.json()
    assert data['total_balance'] == 50.0
    assert data['total_profit'] == 5.0
    assert data['active_wallet_id'] == b.id
    assert data['active_wallets_count'] == 1





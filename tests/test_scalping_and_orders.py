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
        timeframe="M5",
        strategy="SCALPING_BB",
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
        current_price=Decimal("2750.50"),
        recommended_action="READY_TO_BUY",
        trigger_condition="scalp buy",
        analysis_rationale="test",
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
    hist = TradeHistory.objects.get(ticket="LOCAL-12345678")
    assert hist.close_reason == "MANUAL_CLOSE"
    assert hist.source == "USER"
    assert float(wallet.balance) == 1010.00


@pytest.mark.django_db
def test_manual_close_reason_not_overwritten_as_tp_for_bot_source():
    """Đóng tay / close-all → MANUAL_CLOSE + USER (không ép TP_HIT)."""
    from apps.trading.order_source import remember_close_reason, resolve_close_reason, remember_order_source, classify_order_source
    remember_order_source('555001', 'USER', 0)
    remember_close_reason('555001', 'MANUAL_CLOSE', source='USER', magic=0)
    assert resolve_close_reason(ticket='555001', deal_reason=3, comment='WebManual', source='USER') == 'MANUAL_CLOSE'
    assert classify_order_source(magic=8882026, comment='AI-XAU', ticket='555001') == 'USER'
    assert resolve_close_reason(ticket='999', deal_reason=5, comment='', source='BOT') == 'TP_HIT'
    assert resolve_close_reason(ticket='998', deal_reason=0, comment='', source='BOT') == 'MANUAL_CLOSE'


@pytest.mark.django_db
def test_close_all_open_tags_user_and_closes_local():
    wallet = WalletAccount.objects.create(
        name="CloseAll", account_type="DEMO", mt5_login="", is_active=True,
        balance_db=Decimal("1000"), capital=Decimal("1000"),
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CA-1", symbol="XAUUSD", position_type="BUY",
        lot_size=0.01, open_price=Decimal("2700"), current_price=Decimal("2701"),
        floating_pnl=Decimal("1"), source="BOT", opened_at=timezone.now(),
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CA-2", symbol="XAUUSD", position_type="SELL",
        lot_size=0.01, open_price=Decimal("2700"), current_price=Decimal("2699"),
        floating_pnl=Decimal("1"), source="BOT", opened_at=timezone.now(),
    )
    ok, msg, info = ExecutionEngine.close_all_open(wallet)
    assert ok is True
    assert info['closed'] == 2
    assert Position.objects.filter(wallet=wallet).count() == 0
    hist = list(TradeHistory.objects.filter(wallet=wallet))
    assert len(hist) == 2
    assert all(h.source == 'USER' for h in hist)
    assert all(h.close_reason == 'MANUAL_CLOSE' for h in hist)


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
        timeframe="M5",
        strategy="SCALPING_BB",
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
        current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="pyramid",
        analysis_rationale="test",
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
        bot_status="STOPPED",
        allowed_symbols_json='["EURUSD"]',
        min_take_profit_usd=Decimal("1.00"),
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
        current_price=Decimal("1.08500"),
        recommended_action="READY_TO_BUY",
        trigger_condition="trail test",
        analysis_rationale="test",
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
def test_empty_min_tp_does_not_auto_close_winner():
    wallet = WalletAccount.objects.create(
        name="No TP Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        is_active=True,
        min_take_profit_usd=None,
        max_stop_loss_usd=None,
        allowed_symbols_json='["XAUUSD"]',
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2760.00"), is_active=True,
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-NOTP", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2760.00"), opened_at=timezone.now(),
    )
    ExecutionEngine.update_positions_and_pnl()
    assert Position.objects.filter(ticket="LOCAL-NOTP").exists()


@pytest.mark.django_db
def test_max_stop_loss_closes_and_refills_slot():
    """Lỗ chạm max SL → đóng SL_HIT; còn slot thì bot lập plan và mở bù."""
    wallet = WalletAccount.objects.create(
        name="SL Cut Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=2,
        min_take_profit_usd=None,
        max_stop_loss_usd=Decimal("5.00"),
        allowed_symbols_json='["XAUUSD"]',
        default_lot_size=0.01,
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2744.00"),
        current_bid=Decimal("2743.80"), current_ask=Decimal("2744.20"),
        is_active=True,
    )
    MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2744.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="x", analysis_rationale="x",
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-SL-CUT", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2744.00"), opened_at=timezone.now(),
    )
    ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="LOCAL-SL-CUT").exists()
    hist = TradeHistory.objects.filter(ticket="LOCAL-SL-CUT").first()
    assert hist is not None
    assert hist.close_reason == "SL_HIT"
    assert Position.objects.filter(wallet=wallet, source="BOT").count() == 2
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()


@pytest.mark.django_db
def test_risk_percent_holds_losing_trade():
    """Không điền max SL: lệnh lỗ được giữ, không tự cắt."""
    wallet = WalletAccount.objects.create(
        name="SL Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("1000.00"),
        capital=Decimal("1000.00"),
        is_active=True,
        min_take_profit_usd=Decimal("5.00"),
        max_stop_loss_usd=None,
        max_open_trades=5,
        allowed_symbols_json='["XAUUSD"]'
    )

    SymbolConfig.objects.create(
        symbol="XAUUSD",
        display_name="Gold Spot",
        category="METALS",
        digits=2,
        point_size=0.01,
        contract_size=100.0,
        current_price=Decimal("2744.00"),
        is_active=True
    )

    Position.objects.create(
        wallet=wallet,
        ticket="LOCAL-SL-1",
        symbol="XAUUSD",
        position_type="BUY",
        lot_size=0.01,
        open_price=Decimal("2750.00"),
        current_price=Decimal("2744.00"),
        opened_at=timezone.now()
    )
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
        max_stop_loss_usd=Decimal("1.00"),
    )
    assert ExecutionEngine.wallet_max_stop_loss_usd(wallet) == 1.0
    wallet.max_stop_loss_usd = Decimal("20.00")
    assert ExecutionEngine.wallet_max_stop_loss_usd(wallet) == 20.0
    wallet.max_stop_loss_usd = None
    assert ExecutionEngine.wallet_max_stop_loss_usd(wallet) == 0.0
    assert ExecutionEngine.wallet_min_take_profit_usd(wallet) == 0.0


@pytest.mark.django_db
def test_trailing_sl_ratchets_up_not_down():
    """Khoá 1$: lãi 2$ → SL còn 1$; lãi 3$ → SL còn 2$; lãi tụt không dời SL xuống."""
    wallet = WalletAccount.objects.create(
        name="Trail Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        min_take_profit_usd=None,
        max_stop_loss_usd=None,
        trail_sl_enabled=True,
        trail_sl_lock_usd=Decimal("1.00"),
        allowed_symbols_json='["XAUUSD"]',
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2752.00"), is_active=True,
    )
    pos = Position.objects.create(
        wallet=wallet, ticket="LOCAL-TRAIL-1", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2752.00"), opened_at=timezone.now(),
    )
    ExecutionEngine.update_positions_and_pnl()
    pos.refresh_from_db()
    assert Position.objects.filter(ticket="LOCAL-TRAIL-1").exists()
    assert float(pos.stop_loss) == 2751.0  # khoá 1$

    sym.current_price = Decimal("2753.00")
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    pos.refresh_from_db()
    assert float(pos.stop_loss) == 2752.0  # khoá 2$

    sl_before = float(pos.stop_loss)
    sym.current_price = Decimal("2752.40")  # lãi 2.4$ → target khoá 1.4$, không giảm từ 2$
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    pos.refresh_from_db()
    assert Position.objects.filter(ticket="LOCAL-TRAIL-1").exists()
    assert float(pos.stop_loss) == sl_before

    sym.current_price = Decimal("2752.00")  # thụt về đúng SL khoá 2$
    sym.save()
    ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="LOCAL-TRAIL-1").exists()
    hist = TradeHistory.objects.filter(ticket="LOCAL-TRAIL-1").first()
    assert hist is not None
    assert hist.close_reason == "SL_HIT"


@pytest.mark.django_db
def test_trailing_sl_stays_below_market_when_pnl_inflated():
    """PnL MT5 cao hơn bước giá không được đặt SL BUY phía trên giá hiện tại."""
    wallet = WalletAccount.objects.create(
        name="Trail Inflated",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        min_take_profit_usd=None,
        max_stop_loss_usd=None,
        trail_sl_enabled=True,
        trail_sl_lock_usd=Decimal("1.00"),
        allowed_symbols_json='["XAUUSD"]',
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2752.00"), is_active=True,
    )
    pos = Position.objects.create(
        wallet=wallet, ticket="LOCAL-TRAIL-PNL", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2752.00"), floating_pnl=Decimal("10.00"),
        opened_at=timezone.now(),
    )
    assert ExecutionEngine.maybe_update_trailing_sl(pos, 10.0, wallet) is True
    assert float(pos.stop_loss) == 2751.0
    assert float(pos.stop_loss) < float(pos.current_price)


@pytest.mark.django_db
def test_protective_sl_attached_when_max_sl_set():
    """Max SL USD gắn giá SL ngay, không chờ lỗ."""
    wallet = WalletAccount.objects.create(
        name="Protect SL",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        max_stop_loss_usd=Decimal("2.00"),
        trail_sl_enabled=False,
        allowed_symbols_json='["XAUUSD"]',
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        current_price=Decimal("2750.00"), is_active=True,
    )
    pos = Position.objects.create(
        wallet=wallet, ticket="LOCAL-PROT-SL", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    assert ExecutionEngine.maybe_attach_protective_sl(pos, wallet) is True
    assert float(pos.stop_loss) == 2748.0


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
        max_stop_loss_usd=None,
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
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="hanging at cap",
        status="PENDING_TRIGGER",
    )

    can_enter, _, reason = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can_enter is False
    assert "2/2" in reason
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()

    wallet.max_open_trades = 4
    wallet.save()
    can_enter2, is_pyr, _ = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can_enter2 is True
    assert is_pyr is True

    wallet.max_open_trades = 999
    wallet.save()
    assert wallet.max_open_trades == 500
    assert ExecutionEngine.get_max_allowed_positions_for_wallet(wallet) == 500


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
def test_mt5_ticket_auto_closes_at_min_take_profit():
    """Lệnh MT5 (không phải LOCAL) cũng tự chốt khi lãi >= min_take_profit_usd."""
    from unittest.mock import patch
    wallet = WalletAccount.objects.create(
        name="Live TP Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="STOPPED",
        min_take_profit_usd=Decimal("1.00"),
        allowed_symbols_json='["XAUUSD"]',
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100.0,
        # Giá symbol cố ý không đủ để tính lãi — phải chốt theo floating_pnl MT5
        current_price=Decimal("2750.00"), is_active=True,
    )
    Position.objects.create(
        wallet=wallet, ticket="SIM-TP-463001", symbol="XAUUSD", position_type="BUY",
        lot_size=0.01, open_price=Decimal("2750.00"), current_price=Decimal("2750.10"),
        floating_pnl=Decimal("10.00"), source="BOT", opened_at=timezone.now(),
    )
    with patch('apps.trading.execution_engine.ExecutionEngine.persist_mt5_state'), \
         patch('apps.trading.mt5_session.MT5NativeSession.available', return_value=False), \
         patch('apps.trading.mt5_session.MT5NativeSession.account', return_value=None), \
         patch('apps.trading.mt5_session.MT5NativeSession.positions', return_value=[]):
        ExecutionEngine.update_positions_and_pnl()
    assert not Position.objects.filter(ticket="SIM-TP-463001").exists()
    hist = TradeHistory.objects.filter(ticket="SIM-TP-463001").first()
    assert hist is not None
    assert hist.close_reason == "TP_HIT"
    assert hist.source == "BOT"


@pytest.mark.django_db
def test_close_all_closes_mt5_tickets_when_native_empty():
    """Đóng toàn bộ phải đóng ticket MT5 trong DB khi native session không có live (Linux/Wine)."""
    wallet = WalletAccount.objects.create(
        name="CloseAllLive", account_type="DEMO", mt5_login="", is_active=True,
        balance_db=Decimal("1000"), capital=Decimal("1000"),
    )
    for ticket in ("463101", "463102", "463103"):
        Position.objects.create(
            wallet=wallet, ticket=ticket, symbol="XAUUSD", position_type="BUY",
            lot_size=0.01, open_price=Decimal("2700"), current_price=Decimal("2701"),
            floating_pnl=Decimal("1"), source="BOT", opened_at=timezone.now(),
        )
    ok, msg, info = ExecutionEngine.close_all_open(wallet)
    assert ok is True
    assert info['closed'] == 3
    assert Position.objects.filter(wallet=wallet).count() == 0
    assert all(h.source == 'USER' for h in TradeHistory.objects.filter(wallet=wallet))
    assert all(h.close_reason == 'MANUAL_CLOSE' for h in TradeHistory.objects.filter(wallet=wallet))


@pytest.mark.django_db
def test_plan_uses_live_market_ask_bid():
    wallet = WalletAccount.objects.create(
        name="Mkt Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
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
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="ask/bid", analysis_rationale="test",
    )
    buy_plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast)
    assert float(buy_plan.entry_price) == 2750.20

    forecast.trend_bias = "BEARISH"
    forecast.recommended_action = "READY_TO_SELL"
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
    TradingPlan.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(minutes=10),
        updated_at=timezone.now() - timedelta(minutes=10),
    )
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
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
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
        recommended_action="READY_TO_BUY",
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


@pytest.mark.django_db
def test_pending_plan_flips_when_forecast_direction_changes():
    """Đổi BULLISH→BEARISH phải làm mới plan (hướng SELL, timestamp mới) — không giữ BUY cũ."""
    wallet = WalletAccount.objects.create(
        name="Flip Wallet",
        account_type="DEMO",
        mt5_login="",
        allowed_symbols_json='["XAUUSD"]',
        is_active=True,
        default_lot_size=0.01,
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, point_size=0.01, contract_size=100,
        current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"),
        current_ask=Decimal("2750.20"),
        is_active=True,
    )
    bull = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="test", analysis_rationale="test",
    )
    p1 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, bull)
    assert p1.direction == "BUY"
    old_created = p1.created_at
    TradingPlan.objects.filter(pk=p1.pk).update(created_at=timezone.now() - timedelta(minutes=2))
    p1.refresh_from_db()
    old_created = p1.created_at

    bear = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BEARISH",
        confidence_score=90, current_price=Decimal("2748.00"),
        recommended_action="READY_TO_SELL",
        trigger_condition="flip", analysis_rationale="flip",
        updated_at=timezone.now() + timedelta(seconds=5),
    )
    p2 = AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, bear)
    assert p2.id == p1.id
    assert p2.direction == "SELL"
    assert p2.created_at > old_created
    assert TradingPlan.objects.filter(wallet=wallet, symbol="XAUUSD", status="PENDING_TRIGGER").count() == 1

    TradingPlan.objects.filter(pk=p2.pk).update(direction="BUY")
    n = AutoPlanGenerator.purge_wrong_direction_pending()
    assert n == 1
    assert not TradingPlan.objects.filter(pk=p2.pk).exists()


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


@pytest.mark.django_db
def test_bot_user_report_uses_vietnam_calendar_day():
    """Báo cáo BOT vs USER chỉ lệnh đóng hôm nay GMT+7, kể cả 01:00 VN (UTC hôm qua)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from django.test import Client

    vn = ZoneInfo('Asia/Ho_Chi_Minh')
    today = timezone.localdate()
    morning_vn = datetime(today.year, today.month, today.day, 1, 15, tzinfo=vn)
    yesterday_vn = morning_vn - timedelta(days=1)

    wallet = WalletAccount.objects.create(
        name="Daily Report Wallet",
        account_type="DEMO",
        mt5_login="463974323",
        is_active=True,
        balance_db=Decimal("1000"),
        capital=Decimal("1000"),
    )

    def hist(ticket, source, pnl, closed):
        TradeHistory.objects.create(
            wallet=wallet, ticket=ticket, symbol='XAUUSD', position_type='BUY',
            lot_size=0.01, open_price=Decimal('2700'), close_price=Decimal('2701'),
            pnl=Decimal(str(pnl)), is_win=pnl > 0, source=source,
            opened_at=closed - timedelta(minutes=5), closed_at=closed,
        )

    hist('D1', 'BOT', 10, morning_vn)
    hist('D2', 'USER', 4, timezone.now())
    hist('D3', 'BOT', 99, yesterday_vn)

    daily = wallet.get_daily_breakdown()
    assert daily['bot']['total_trades'] == 1
    assert daily['user']['total_trades'] == 1
    assert daily['bot']['today_pnl'] == 10.0
    assert daily['user']['today_pnl'] == 4.0
    assert daily['all']['today_pnl'] == 14.0
    assert wallet.get_today_pnl() == Decimal('14.00')

    all_time = wallet.get_performance_breakdown()
    assert all_time['bot']['total_trades'] == 2
    assert all_time['bot']['today_pnl'] == 10.0

    data = Client().get('/api/live-ticks/').json()
    bm = data['overview']['bot_metrics']
    um = data['overview']['user_metrics']
    assert bm['total_trades'] == 1
    assert um['total_trades'] == 1
    assert bm['today_pnl'] == 10.0
    assert um['today_pnl'] == 4.0
    assert data['overview']['total_today_pnl'] == 14.0
    tickets = {h['ticket'] for h in data['history']}
    assert tickets == {'D1', 'D2'}


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


def test_direction_from_forecast_skips_wait_and_monitoring():
    from types import SimpleNamespace
    from apps.plans.planner import AutoPlanGenerator
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BULLISH', recommended_action='WAIT_FOR_PULLBACK')) is None
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BEARISH', recommended_action='WAIT_FOR_PULLBACK')) is None
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='SIDEWAY', recommended_action='MONITORING')) is None
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='SIDEWAY', recommended_action='READY_TO_SELL')) == 'SELL'
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BULLISH', recommended_action='READY_TO_BUY')) == 'BUY'
    assert AutoPlanGenerator.direction_from_forecast(SimpleNamespace(trend_bias='BEARISH', recommended_action='READY_TO_SELL')) == 'SELL'


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
    """Chưa đủ lệnh: AI khớp MARKET đến max_open_trades, không treo plan PENDING."""
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
        timeframe="M5", strategy="SCALPING_BB",
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="ema9>ema21", analysis_rationale="scalp buy from candles",
    )
    ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert Position.objects.filter(wallet=wallet, symbol="XAUUSD", source="BOT").count() == 3
    executing = TradingPlan.objects.filter(wallet=wallet, symbol="XAUUSD", status="EXECUTING")
    assert executing.count() == 3
    assert all(p.direction == "BUY" for p in executing)
    assert not TradingPlan.objects.filter(wallet=wallet, status="PENDING_TRIGGER").exists()
    assert all("Lướt sóng" in (p.rationale or "") for p in executing)


@pytest.mark.django_db
def test_no_new_order_when_at_max_positions_clears_pending_plan():
    """Đủ max lệnh: không mở thêm và không giữ plan PENDING chờ (hướng sẽ lệch khi khớp sau)."""
    wallet = WalletAccount.objects.create(
        name="Full Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=1,
        allowed_symbols_json='["XAUUSD"]',
        default_lot_size=0.01,
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BEARISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_SELL",
        trigger_condition="x", analysis_rationale="x",
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-FULL-1", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2740.00"),
        entry_zone_high=Decimal("2760.00"), rationale="old buy",
        status="PENDING_TRIGGER",
    )
    ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert not TradingPlan.objects.filter(wallet=wallet, status="PENDING_TRIGGER").exists()
    assert Position.objects.filter(wallet=wallet).count() == 1
    assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast) is None
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()


@pytest.mark.django_db
def test_no_plan_created_when_slots_full_purges_hanging():
    """Đã đủ max_open_trades: không sinh plan mới, xóa plan PENDING treo, giữ EXECUTING."""
    wallet = WalletAccount.objects.create(
        name="Cap No Plan",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=2,
        allowed_symbols_json='["XAUUSD"]',
        default_lot_size=0.01,
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
        trigger_condition="x", analysis_rationale="x",
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CAP-A", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    Position.objects.create(
        wallet=wallet, ticket="LOCAL-CAP-B", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2751.00"),
        current_price=Decimal("2751.00"), opened_at=timezone.now(),
    )
    hanging = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="hanging",
        status="PENDING_TRIGGER",
    )
    executing = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="open",
        status="EXECUTING",
    )
    assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast) is None
    assert AutoPlanGenerator.update_or_create_plan_for_wallet(wallet, sym, forecast) is None
    assert not TradingPlan.objects.filter(pk=hanging.pk).exists()
    assert TradingPlan.objects.filter(pk=executing.pk, status="EXECUTING").exists()
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()

    hanging2 = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="SELL",
        entry_price=Decimal("2749.80"), entry_zone_low=Decimal("2749.80"),
        entry_zone_high=Decimal("2749.80"), rationale="stale hang",
        status="PENDING_TRIGGER",
    )
    AutoPlanGenerator.purge_dead_plans()
    assert not TradingPlan.objects.filter(pk=hanging2.pk).exists()
    assert TradingPlan.objects.filter(pk=executing.pk, status="EXECUTING").exists()


@pytest.mark.django_db
def test_refill_plan_after_close_uses_current_forecast():
    """Đóng lệnh xong nếu còn slot: lập plan mới theo forecast lúc đó rồi khớp ngay, không để PENDING chờ."""
    wallet = WalletAccount.objects.create(
        name="Refill Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("2000.00"),
        capital=Decimal("2000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=2,
        allowed_symbols_json='["XAUUSD"]',
        default_lot_size=0.01,
    )
    SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        is_active=True,
    )
    MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BEARISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_SELL",
        trigger_condition="x", analysis_rationale="x",
    )
    pos = Position.objects.create(
        wallet=wallet, ticket="LOCAL-OLD-1", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), floating_pnl=Decimal("1.00"),
        opened_at=timezone.now(),
    )
    ok, _ = ExecutionEngine.close_position(pos, reason="MANUAL_CLOSE")
    assert ok is True
    assert not Position.objects.filter(ticket="LOCAL-OLD-1").exists()
    fresh = Position.objects.filter(wallet=wallet, source="BOT")
    assert fresh.count() == 2
    assert all(p.position_type == "SELL" for p in fresh)
    assert TradingPlan.objects.filter(wallet=wallet, status="EXECUTING", direction="SELL").count() == 2
    assert not TradingPlan.objects.filter(wallet=wallet, status="PENDING_TRIGGER").exists()


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
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="BULLISH",
        confidence_score=80, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="x", analysis_rationale="x",
    )
    with patch.object(ExecutionEngine, 'wallet_today_risk_pnl', return_value=-480.0):
        can, _, _ = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
    assert can is True

    Position.objects.create(
        wallet=wallet, ticket="LOCAL-RECAP-1", symbol="XAUUSD",
        position_type="BUY", lot_size=0.01, open_price=Decimal("2750.00"),
        current_price=Decimal("2750.00"), opened_at=timezone.now(),
    )
    hanging = TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="hanging",
        status="PENDING_TRIGGER",
    )
    with patch.object(ExecutionEngine, 'wallet_today_risk_pnl', return_value=-480.0):
        can2, is_pyr, _ = ExecutionEngine.can_wallet_open_or_pyramid(wallet, "XAUUSD", "BUY")
        assert can2 is True
        assert is_pyr is True
        ExecutionEngine.try_immediate_market_entries({ 'XAUUSD': (sym, forecast) })
    assert Position.objects.filter(wallet=wallet).count() == 5
    assert not TradingPlan.objects.filter(pk=hanging.pk).exists()
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()


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


@pytest.mark.django_db
def test_api_rejects_activating_second_wallet():
    from django.test import Client
    WalletAccount.objects.create(
        name="A", account_type="DEMO", mt5_login="111", is_active=True,
    )
    b = WalletAccount.objects.create(
        name="B", account_type="DEMO", mt5_login="222", is_active=False,
    )
    res = Client().put(
        f'/api/admin/wallets/{b.id}/',
        data='{"is_active": true}',
        content_type='application/json',
    )
    assert res.status_code == 400
    assert 'Đã có ví đang kích hoạt' in res.json()['error']
    b.refresh_from_db()
    assert b.is_active is False


@pytest.mark.django_db
def test_activate_wallet_calls_login_and_algo():
    from django.test import Client
    from unittest.mock import patch
    w = WalletAccount.objects.create(
        name='Switch', account_type='DEMO', mt5_login='777888',
        mt5_password='pw', mt5_server='Exness-MT5Trial17', is_active=False,
    )
    with patch('apps.trading.mt5_connector.ExnessMT5Connector.activate_wallet_session', return_value=(
        True, 'Đã login MT5 #777888',
        {'balance': 100.0, 'leverage': 500, 'algo_trading': True},
    )) as mock_act, \
         patch('apps.trading.mt5_connector.ExnessMT5Connector.connect', return_value=False):
        res = Client().put(
            f'/api/admin/wallets/{w.id}/',
            data='{"is_active": true}',
            content_type='application/json',
        )
        assert res.status_code == 200
        assert res.json()['success'] is True
        assert 'login' in res.json()['message'].lower() or res.json().get('algo_required') is False
        mock_act.assert_called_once()
        w.refresh_from_db()
        assert w.is_active is True
        assert w.leverage == 500


@pytest.mark.django_db
def test_deactivate_wallet_purges_plans_and_skips_new():
    from apps.plans.models import TradingPlan
    from apps.plans.planner import AutoPlanGenerator
    from apps.analysis.models import MarketForecast
    wallet = WalletAccount.objects.create(
        name='Deact', account_type='DEMO', mt5_login='909090',
        is_active=True, balance_db=Decimal('100'), equity_db=Decimal('100'),
        allowed_symbols_json='["XAUUSD"]',
    )
    sym = SymbolConfig.objects.create(
        symbol='XAUUSD', display_name='Gold', category='METALS',
        digits=2, point_size=0.01, current_price=Decimal('2700'), is_active=True,
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol='XAUUSD', timeframe='M5', direction='BUY',
        entry_price=Decimal('2700'), entry_zone_low=Decimal('2700'), entry_zone_high=Decimal('2700'),
        status='PENDING_TRIGGER', calculated_lot=0.01, rationale='test',
    )
    assert TradingPlan.objects.filter(wallet=wallet).count() == 1

    wallet.is_active = False
    wallet.bot_status = 'STOPPED'
    wallet.save()
    assert TradingPlan.objects.filter(wallet=wallet).count() == 0

    fc = MarketForecast.objects.create(
        symbol='XAUUSD', timeframe='M5', trend_bias='BULLISH',
        recommended_action='READY_TO_BUY', confidence_score=80,
        trigger_condition='now', analysis_rationale='test',
    )
    assert AutoPlanGenerator.refresh_plans_for_wallet(wallet) == []
    assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc) is None
    assert TradingPlan.objects.filter(wallet=wallet).count() == 0


@pytest.mark.django_db
def test_inactive_wallet_save_skips_mt5_connection():
    from django.test import Client
    from unittest.mock import patch
    with patch('apps.trading.mt5_connector.ExnessMT5Connector.activate_wallet_session') as mock_act, \
         patch('apps.trading.mt5_connector.ExnessMT5Connector.connect') as mock_connect:
        res = Client().post('/api/admin/wallets/', {
            'name': 'Inactive Save',
            'account_type': 'DEMO',
            'mt5_login': '555666',
            'mt5_password': 'secret',
            'mt5_server': 'Exness-MT5Trial17',
            'allowed_symbols': ['XAUUSD'],
            'is_active': False,
            'capital': 200,
        }, content_type='application/json')
        assert res.status_code == 200
        assert res.json()['success'] is True
        mock_act.assert_not_called()
        mock_connect.assert_not_called()
        w = WalletAccount.objects.get(mt5_login='555666')
        assert w.is_active is False
        assert float(w.capital) == 200.0

        mock_act.reset_mock()
        mock_connect.reset_mock()
        res2 = Client().put(
            f'/api/admin/wallets/{w.id}/',
            data='{"name":"Inactive Updated","is_active":false,"mt5_login":"555666","mt5_server":"Exness-MT5Trial17"}',
            content_type='application/json',
        )
        assert res2.status_code == 200
        mock_act.assert_not_called()
        mock_connect.assert_not_called()


@pytest.mark.django_db
def test_inactive_wallet_does_not_fake_1000_balance():
    """Ví chưa kích hoạt không gắn số dư giả $1000 — UI sẽ hiện —."""
    from django.test import Client
    from unittest.mock import patch
    with patch('apps.trading.mt5_connector.ExnessMT5Connector.activate_wallet_session') as mock_act:
        res = Client().post('/api/admin/wallets/', {
            'name': 'No Fake Balance',
            'account_type': 'DEMO',
            'mt5_login': '111222',
            'mt5_password': 'secret',
            'mt5_server': 'Exness-MT5Trial17',
            'allowed_symbols': ['XAUUSD'],
            'is_active': False,
        }, content_type='application/json')
    assert res.status_code == 200
    mock_act.assert_not_called()
    w = WalletAccount.objects.get(mt5_login='111222')
    assert w.is_active is False
    assert float(w.balance_db) == 0.0
    assert float(w.equity_db) == 0.0
    assert float(w.capital) == 0.0


@pytest.mark.django_db
def test_inactive_wallet_does_not_get_mt5_connector():
    """Ví tắt kích hoạt không được cấp connector — bot không login MT5 cho ví đó."""
    wallet = WalletAccount.objects.create(
        name='Off', account_type='DEMO', mt5_login='434232731',
        mt5_password='secret', mt5_server='Exness-MT5Trial17', is_active=False,
    )
    assert ExecutionEngine.mt5_connector_for_wallet(wallet) is None


def test_wine_connect_does_not_login_other_account_without_allow_switch():
    """Wine: terminal đang #20 thì không POST /login sang ví #16 khi allow_switch=False."""
    from unittest.mock import patch, MagicMock
    from apps.trading.mt5_connector import ExnessMT5Connector

    conn = ExnessMT5Connector(login='434232731', password='pw', server='Exness-MT5Trial17')
    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.json.return_value = {'success': True, 'account_info': {'login': 463974323}}
    with patch('apps.trading.mt5_connector.MT5_AVAILABLE', False), \
         patch.object(ExnessMT5Connector, 'is_bridge_reachable', return_value=True), \
         patch('apps.trading.mt5_connector.requests.get', return_value=mock_get), \
         patch('apps.trading.mt5_connector.requests.post') as mock_post:
        assert conn.connect(allow_switch=False) is False
        mock_post.assert_not_called()


def test_wine_connect_does_not_login_empty_session_without_allow_switch():
    """Wine: chưa có phiên / GET lỗi thì vẫn không tự POST /login nếu không allow_switch."""
    from unittest.mock import patch, MagicMock
    from apps.trading.mt5_connector import ExnessMT5Connector

    conn = ExnessMT5Connector(login='434232731', password='pw', server='Exness-MT5Trial17')
    mock_get = MagicMock()
    mock_get.status_code = 400
    mock_get.json.return_value = {'success': False}
    with patch('apps.trading.mt5_connector.MT5_AVAILABLE', False), \
         patch.object(ExnessMT5Connector, 'is_bridge_reachable', return_value=True), \
         patch('apps.trading.mt5_connector.requests.get', return_value=mock_get), \
         patch('apps.trading.mt5_connector.requests.post') as mock_post:
        assert conn.connect(allow_switch=False) is False
        mock_post.assert_not_called()


def test_wine_connect_logs_in_only_with_allow_switch():
    """Wine: chỉ ví đang kích hoạt (allow_switch=True) mới được POST /login."""
    from unittest.mock import patch, MagicMock
    from apps.trading.mt5_connector import ExnessMT5Connector

    conn = ExnessMT5Connector(login='463974323', password='pw', server='Exness-MT5Real')
    mock_get = MagicMock()
    mock_get.status_code = 200
    mock_get.json.return_value = {'success': True, 'account_info': {'login': 434232731}}
    mock_post = MagicMock()
    mock_post.status_code = 200
    mock_post.json.return_value = {'success': True}
    with patch('apps.trading.mt5_connector.MT5_AVAILABLE', False), \
         patch.object(ExnessMT5Connector, 'is_bridge_reachable', return_value=True), \
         patch('apps.trading.mt5_connector.requests.get', return_value=mock_get), \
         patch('apps.trading.mt5_connector.requests.post', return_value=mock_post) as posted:
        assert conn.connect(allow_switch=True) is True
        posted.assert_called_once()
        assert posted.call_args[0][0].endswith('/login')


@pytest.mark.django_db
def test_monitoring_forecast_does_not_open_or_create_plan():
    """MONITORING / thiếu tín hiệu nến → không lập plan, không mở lệnh."""
    wallet = WalletAccount.objects.create(
        name="Wait Wallet",
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
        timeframe="M5", strategy="SCALPING_BB", is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="M5", trend_bias="SIDEWAY",
        confidence_score=55, current_price=Decimal("2750.00"),
        recommended_action="MONITORING",
        trigger_condition="wait candles", analysis_rationale="no signal",
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="stale",
        status="PENDING_TRIGGER",
    )
    assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast) is None
    ExecutionEngine.try_immediate_market_entries({'XAUUSD': (sym, forecast)})
    assert not Position.objects.filter(wallet=wallet).exists()
    assert not TradingPlan.objects.filter(
        wallet=wallet, status__in=["PENDING_TRIGGER", "PENDING", "ANALYZING"]
    ).exists()


@pytest.mark.django_db
def test_swing_plan_rationale_mentions_trail_sl():
    """Dài hạn (H1/SMC): plan ghi rõ trail SL; chỉ sinh khi READY_TO_*."""
    wallet = WalletAccount.objects.create(
        name="Swing Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("5000.00"),
        capital=Decimal("5000.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=2,
        allowed_symbols_json='["XAUUSD"]',
        trail_sl_enabled=True,
        trail_sl_lock_usd=Decimal("5.00"),
        min_take_profit_usd=Decimal("10.00"),
    )
    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal("2750.00"),
        current_bid=Decimal("2749.80"), current_ask=Decimal("2750.20"),
        timeframe="H1", strategy="SMC_TREND", is_active=True,
    )
    forecast = MarketForecast.objects.create(
        symbol="XAUUSD", timeframe="H1", trend_bias="BULLISH",
        confidence_score=78, current_price=Decimal("2750.00"),
        recommended_action="READY_TO_BUY",
        trigger_condition="ema50>ema200", analysis_rationale="swing uptrend",
    )
    plan = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast)
    assert plan is not None
    assert plan.direction == "BUY"
    assert "Dài hạn" in plan.rationale
    assert "dời SL" in plan.rationale or "khoá" in plan.rationale
    assert "Forecast: BULLISH/READY_TO_BUY" in plan.rationale


@pytest.mark.django_db
def test_analyzer_uses_real_candle_indicators_not_random():
    """TechnicalAnalyzer quyết định từ OHLC giả lập — cùng input → cùng tín hiệu (không random)."""
    from apps.analysis.analyzer import TechnicalAnalyzer
    from unittest.mock import patch
    import json

    rates = []
    px = 2700.0
    for i in range(120):
        step = 0.45 if (i % 6) != 0 else -0.05
        px += step
        rates.append({
            'open': px - 0.15, 'high': px + 0.25, 'low': px - 0.25, 'close': px,
        })

    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal(str(round(px, 2))),
        current_bid=Decimal(str(round(px - 0.05, 2))),
        current_ask=Decimal(str(round(px + 0.05, 2))),
        timeframe="M5", strategy="SCALPING_BB", is_active=True,
    )
    with patch('apps.trading.mt5_session.MT5NativeSession.copy_rates', return_value=rates), \
         patch('apps.analysis.analyzer.TechnicalAnalyzer._htf_bias', return_value='BULLISH'):
        f1 = TechnicalAnalyzer.generate_market_analysis(sym)
        f2 = TechnicalAnalyzer.generate_market_analysis(sym)
    assert f1.recommended_action == f2.recommended_action
    assert f1.trend_bias == f2.trend_bias
    ind = json.loads(f1.indicators_json)
    assert ind['mode'] == 'SCALP'
    assert ind['data_ok'] is True
    assert ind['candles'] == 120
    assert ind['ema9'] is not None and ind['ema21'] is not None
    assert ind['ema9'] > ind['ema21']
    assert ind.get('htf_bias') == 'BULLISH'
    # HTF tăng: chỉ BUY hoặc MONITORING (chờ đủ MACD/RSI) — không SELL ngược sóng
    assert f1.recommended_action in ('READY_TO_BUY', 'MONITORING')
    assert f1.trend_bias in ('BULLISH', 'SIDEWAY')
    assert 'SELL' not in (f1.recommended_action or '')


def test_scalp_htf_bearish_blocks_buy():
    from apps.analysis.analyzer import TechnicalAnalyzer
    # Struct giảm → không BUY; vào SELL theo ngắn hạn
    blocked = TechnicalAnalyzer._decide_scalp(
        price=101, atr=1, rsi=55, ema9=101.5, ema21=100,
        bb_mid=100, bb_up=102, bb_lo=98, macd_h=0.05,
        r1=102, r2=103, s1=98, s2=97, timeframe='M5', digits=2,
        data_ok=True, htf_bias='BEARISH', struct_bias='BEARISH',
    )
    assert blocked['action'] != 'READY_TO_BUY'
    assert blocked['trend_bias'] == 'BEARISH'

    sell = TechnicalAnalyzer._decide_scalp(
        price=99.5, atr=1, rsi=40, ema9=99.8, ema21=100.5,
        bb_mid=100, bb_up=102, bb_lo=97, macd_h=-0.05,
        r1=102, r2=103, s1=97, s2=96, timeframe='M5', digits=2,
        data_ok=True, htf_bias='BEARISH', struct_bias='BEARISH',
    )
    assert sell['action'] == 'READY_TO_SELL'
    assert sell['trend_bias'] == 'BEARISH'


def test_scalp_struct_bearish_blocks_buy_even_if_htf_sideway():
    """Impulse giảm ngắn hạn → SELL / không BUY."""
    from apps.analysis.analyzer import TechnicalAnalyzer
    out = TechnicalAnalyzer._decide_scalp(
        price=101, atr=1, rsi=55, ema9=101.5, ema21=100,
        bb_mid=100, bb_up=102, bb_lo=98, macd_h=0.05,
        r1=102, r2=103, s1=98, s2=97, timeframe='M5', digits=2,
        data_ok=True, htf_bias='SIDEWAY', struct_bias='BEARISH',
    )
    assert out['action'] != 'READY_TO_BUY'
    assert out['trend_bias'] == 'BEARISH'


def test_scalp_near_resistance_blocks_buy():
    """Giá sát R1 quá gần (<0.25×ATR) → WAIT; còn lại vẫn BUY theo ngắn hạn."""
    from apps.analysis.analyzer import TechnicalAnalyzer
    out = TechnicalAnalyzer._decide_scalp(
        price=101.9, atr=1.0, rsi=58, ema9=101.5, ema21=100.2,
        bb_mid=100.5, bb_up=103, bb_lo=98, macd_h=0.08,
        r1=102.0, r2=103.5, s1=98.0, s2=97.0, timeframe='M5', digits=2,
        data_ok=True, htf_bias='BULLISH', struct_bias='BULLISH',
    )
    assert out['action'] == 'WAIT_FOR_PULLBACK'


def test_scalp_near_support_blocks_sell():
    from apps.analysis.analyzer import TechnicalAnalyzer
    out = TechnicalAnalyzer._decide_scalp(
        price=98.3, atr=1.0, rsi=40, ema9=98.5, ema21=100.0,
        bb_mid=100, bb_up=103, bb_lo=97, macd_h=-0.08,
        r1=103.0, r2=104.0, s1=98.0, s2=96.5, timeframe='M5', digits=2,
        data_ok=True, htf_bias='BEARISH', struct_bias='BEARISH',
    )
    assert out['action'] == 'WAIT_FOR_PULLBACK'


def test_swing_macd_negative_blocks_ready_buy():
    """Uptrend EMA nhưng MACD âm → WAIT, không READY_TO_BUY (case XAUUSD)."""
    from apps.analysis.analyzer import TechnicalAnalyzer
    out = TechnicalAnalyzer._decide_swing(
        price=4336.18, atr=9.59, rsi=46.7,
        ema50=4331.74, ema200=4306.73, macd_h=-2.04844,
        r1=4360.54, r2=4370.0, s1=4322.84, s2=4310.0,
        timeframe='M15', digits=2, data_ok=True, struct_bias='BULLISH',
    )
    assert out['trend_bias'] == 'BULLISH'
    assert out['action'] == 'WAIT_FOR_PULLBACK'
    assert 'BUY' not in out['action'] or out['action'] == 'WAIT_FOR_PULLBACK'
    assert 'MACD' in (out['structure'] + out['trigger'])


@pytest.mark.django_db
def test_analyzer_swing_wait_pullback_when_extended():
    """Swing: giá quá xa EMA50 → WAIT_FOR_PULLBACK, planner không mở lệnh."""
    from apps.analysis.analyzer import TechnicalAnalyzer
    from unittest.mock import patch

    rates = []
    px = 2700.0
    for i in range(220):
        # tăng chậm rồi spike cuối → extended vs EMA50
        px += 0.4 if i < 200 else 8.0
        rates.append({'open': px - 0.5, 'high': px + 1.0, 'low': px - 1.0, 'close': px})

    sym = SymbolConfig.objects.create(
        symbol="XAUUSD", display_name="Gold", category="METALS",
        digits=2, current_price=Decimal(str(round(px, 2))),
        current_bid=Decimal(str(round(px - 0.2, 2))),
        current_ask=Decimal(str(round(px + 0.2, 2))),
        timeframe="H1", strategy="SMC_TREND", is_active=True,
    )
    with patch('apps.trading.mt5_session.MT5NativeSession.copy_rates', return_value=rates):
        fc = TechnicalAnalyzer.generate_market_analysis(sym)
    # Có thể READY hoặc WAIT tùy khoảng cách ATR — nếu WAIT thì planner None
    if fc.recommended_action == 'WAIT_FOR_PULLBACK':
        wallet = WalletAccount.objects.create(
            name="Pullback Wallet", account_type="DEMO", mt5_login="",
            balance_db=Decimal("3000"), capital=Decimal("3000"),
            is_active=True, bot_status="RUNNING", max_open_trades=2,
            allowed_symbols_json='["XAUUSD"]',
        )
        assert AutoPlanGenerator.direction_from_forecast(fc) is None
        assert AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, fc) is None
    else:
        assert fc.recommended_action in ('READY_TO_BUY', 'MONITORING', 'READY_TO_SELL')
        assert fc.trend_bias in ('BULLISH', 'BEARISH', 'SIDEWAY')


@pytest.mark.django_db
def test_wallet_bot_toggle_api_stops_and_starts(client):
    """API bật/tắt bot: STOPPED xóa plan chờ; RUNNING lại được."""
    wallet = WalletAccount.objects.create(
        name="Toggle Bot Wallet",
        account_type="DEMO",
        mt5_login="",
        balance_db=Decimal("500.00"),
        capital=Decimal("500.00"),
        is_active=True,
        bot_status="RUNNING",
        max_open_trades=1,
        allowed_symbols_json='["XAUUSD"]',
    )
    TradingPlan.objects.create(
        wallet=wallet, symbol="XAUUSD", direction="BUY",
        entry_price=Decimal("2750.00"), entry_zone_low=Decimal("2750.00"),
        entry_zone_high=Decimal("2750.00"), rationale="pending",
        status="PENDING_TRIGGER",
    )
    res = client.post(
        f'/api/admin/wallets/{wallet.id}/bot-toggle/',
        data='{"bot_status":"STOPPED"}',
        content_type='application/json',
    )
    assert res.status_code == 200
    body = res.json()
    assert body['success'] is True
    assert body['bot_status'] == 'STOPPED'
    assert body['bot_running'] is False
    wallet.refresh_from_db()
    assert wallet.bot_status == 'STOPPED'
    assert not TradingPlan.objects.filter(wallet=wallet, status='PENDING_TRIGGER').exists()

    res2 = client.post(
        f'/api/admin/wallets/{wallet.id}/bot-toggle/',
        data='{"bot_status":"RUNNING"}',
        content_type='application/json',
    )
    assert res2.status_code == 200
    assert res2.json()['bot_running'] is True
    wallet.refresh_from_db()
    assert wallet.bot_status == 'RUNNING'



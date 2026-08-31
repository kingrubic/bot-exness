import pytest
from decimal import Decimal
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
    assert "Trend" in plan1.rationale

    # 2. Lệnh 2 nhồi theo Trend (Pyramiding)
    plan2 = AutoPlanGenerator.generate_plan_for_wallet(wallet, sym, forecast, is_pyramiding=True)
    assert plan2 is not None
    assert "Trend" in plan2.rationale
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




"""Fixtures for plan lifecycle unit tests; real entry checks live in test_entry_safety."""
import pytest


@pytest.fixture(autouse=True)
def _clear_analyzer_rates_cache():
    """Multi-timeframe candles are cached per symbol; keep tests from sharing them."""
    from apps.analysis.analyzer import clear_rates_cache
    clear_rates_cache()
    yield
    clear_rates_cache()


@pytest.fixture
def approved_entry(monkeypatch):
    """Isolate storage/refill mechanics from separately tested signal/risk validation."""
    def preview(wallet, symbol, forecast):
        direction = 'SELL' if forecast.recommended_action == 'READY_TO_SELL' else 'BUY'
        price = float(symbol.current_ask if direction == 'BUY' else symbol.current_bid)
        sign = 1 if direction == 'BUY' else -1
        return dict(
            allowed=True, entry_price=price, stop_loss=price - sign * .01,
            take_profit_1=price + sign * .01,
            take_profit_2=price + sign * .02,
            target_price=price + sign * .02, rr_ratio=2, risk_amount_usd=1,
            calculated_lot=wallet.default_lot_size,
        )
    monkeypatch.setattr('apps.plans.planner.entry_preview', preview)

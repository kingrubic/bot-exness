"""Fixtures for plan lifecycle unit tests; real entry checks live in test_entry_safety."""
import pytest


@pytest.fixture
def approved_entry(monkeypatch):
    """Isolate storage/refill mechanics from separately tested signal/risk validation."""
    def preview(wallet, symbol, forecast):
        direction = 'SELL' if forecast.recommended_action == 'READY_TO_SELL' else 'BUY'
        price = float(symbol.current_ask if direction == 'BUY' else symbol.current_bid)
        sign = 1 if direction == 'BUY' else -1
        return dict(allowed=True, entry_price=price, stop_loss=price - sign * .01,
                    target_price=price + sign * .02, rr_ratio=2, risk_amount_usd=1,
                    calculated_lot=wallet.default_lot_size)
    monkeypatch.setattr('apps.plans.planner.entry_preview', preview)

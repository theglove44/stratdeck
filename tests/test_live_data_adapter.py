from types import SimpleNamespace

import pytest

from stratdeck.tools import chains, greeks
from stratdeck.tools.chain_pricing_adapter import ChainPricingAdapter


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def get_option_chain(self, symbol: str, expiry: str = None):
        self.calls += 1
        expiry = expiry or "2024-12-31"
        puts = [
            {
                "type": "put",
                "strike": 95.0,
                "bid": 0.30,
                "ask": 0.50,
                "mid": 0.40,
                "delta": 0.15,
                "greeks": {"delta": 0.15, "theta": -1.0, "vega": 2.0, "gamma": 0.01},
            },
            {
                "type": "put",
                "strike": 100.0,
                "bid": 1.00,
                "ask": 1.20,
                "mid": 1.10,
                "delta": 0.25,
                "greeks": {"delta": 0.25, "theta": -1.2, "vega": 2.3, "gamma": 0.02},
            },
        ]
        return {"symbol": symbol, "expiry": expiry, "puts": puts, "calls": []}


@pytest.fixture
def use_fake_provider():
    original = chains._provider
    fake = FakeProvider()
    chains.set_provider(fake)
    yield fake
    chains.set_provider(original)


def test_chain_pricing_adapter_uses_provider_mid(use_fake_provider):
    adapter = ChainPricingAdapter()
    legs = [
        SimpleNamespace(type="put", side="short", strike=100.0),
        SimpleNamespace(type="put", side="long", strike=95.0),
    ]
    pricing = adapter.price_structure(
        symbol="SPY",
        strategy_type="short_put_spread",
        legs=legs,
        dte_target=30,
    )
    assert pricing is not None
    assert pricing["credit"] == pytest.approx(0.70)
    assert pricing["credit_per_width"] == pytest.approx(0.14)
    # delta 0.25 -> base pop 0.75, bonus 0.01 for 5-point width
    assert pricing["pop"] == pytest.approx(0.76)


def test_greeks_calc_combines_chain_quotes(use_fake_provider):
    legs = [
        {"type": "put", "side": "short", "strike": 100.0, "qty": 1},
        {"type": "put", "side": "long", "strike": 95.0, "qty": 1},
    ]
    totals = greeks.calc("SPY", "2024-12-31", legs)
    assert totals["delta"] == pytest.approx(-0.10)
    assert totals["theta"] == pytest.approx(0.2)
    assert totals["vega"] == pytest.approx(-0.3)
    assert totals["gamma"] == pytest.approx(-0.01)

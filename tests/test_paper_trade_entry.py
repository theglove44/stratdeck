import pytest

from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
from stratdeck.tools import orders, positions


class FakePricingAdapter:
    def __init__(self):
        self.calls = []

    def price_structure(self, symbol, strategy_type, legs, dte_target, target_delta_hint=None):
        self.calls.append(
            {
                "symbol": symbol,
                "strategy": strategy_type,
                "dte_target": dte_target,
                "target_delta": target_delta_hint,
                "legs": legs,
            }
        )
        return {
            "credit": 0.70,
            "credit_per_width": 0.14,
            "pop": 0.76,
            "width": 5.0,
            "legs": {
                "short": {"mid": 1.10, "strike": 100.0, "type": "put", "side": "short"},
                "long": {"mid": 0.40, "strike": 95.0, "type": "put", "side": "long"},
            },
            "expiry": "2099-01-01",
        }


def test_enter_paper_trade_logs_position(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATDECK_TRADING_MODE", "paper")
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    monkeypatch.setattr(positions, "POS_PATH", tmp_path / ".stratdeck" / "positions.json")

    fake_pricing = FakePricingAdapter()
    idea = TradeIdea(
        symbol="SPY",
        data_symbol="SPY",
        trade_symbol="SPY",
        strategy="short_put_spread",
        direction="bullish",
        vol_context="normal",
        rationale="test entry",
        legs=[
            TradeLeg(side="short", type="put", strike=100.0, expiry="2099-01-01", quantity=1),
            TradeLeg(side="long", type="put", strike=95.0, expiry="2099-01-01", quantity=1),
        ],
        spread_width=5.0,
        dte_target=10,
        notes=["[provenance] template=short_put_spread_index_45d universe=index_core"],
        pop=0.76,
        credit_per_width=0.14,
        estimated_credit=0.70,
    )

    result = orders.enter_paper_trade(idea, qty=2, pricing_client=fake_pricing)

    assert fake_pricing.calls, "pricing adapter should be invoked for mid prices"
    assert result["entry_mid_price"] == pytest.approx(0.70)
    assert result["total_credit"] == pytest.approx(140.0)
    assert result["credit_per_width"] == pytest.approx(0.14)
    assert result["trading_mode"] == "paper"

    rows = positions.list_positions()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "SPY"
    assert row["strategy"] == "short_put_spread"
    assert row["direction"] == "bullish"
    assert row["entry_mid_price"] == pytest.approx(0.70)
    assert row["credit"] == pytest.approx(0.70)
    assert row["qty"] == 2
    assert row["dte"] == positions._calc_dte("2099-01-01")
    assert row["status"] == "open"
    assert "[provenance]" in (row.get("provenance") or "")

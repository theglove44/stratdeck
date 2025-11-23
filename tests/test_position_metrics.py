from datetime import datetime, timezone

import pytest

from stratdeck.tools.position_monitor import compute_position_metrics, load_exit_rules
from stratdeck.tools.positions import PaperPosition, PaperPositionLeg


class StubProvider:
    def __init__(self, chain_mid: float = 1.0, ivr: float = 42.0):
        self.chain_mid = chain_mid
        self._ivr = ivr

    def get_option_chain(self, symbol: str, expiry: str = None):
        # Two-leg put spread with deterministic mids.
        return {
            "puts": [
                {"strike": 100.0, "mid": self.chain_mid + 0.1},
                {"strike": 95.0, "mid": 0.1},
            ]
        }

    def get_quote(self, symbol: str):
        return {"mark": 410.0}

    def get_ivr(self, symbol: str):
        return self._ivr


def test_compute_position_metrics_credit_spread():
    position = PaperPosition(
        symbol="XSP",
        trade_symbol="XSP",
        strategy_id="short_put_spread_index_45d",
        universe_id="index_core",
        direction="bullish",
        legs=[
            PaperPositionLeg(side="short", type="put", strike=100.0, expiry="2100-01-01", quantity=1),
            PaperPositionLeg(side="long", type="put", strike=95.0, expiry="2100-01-01", quantity=1),
        ],
        qty=1,
        entry_mid=1.5,
        spread_width=5.0,
    )
    provider = StubProvider(chain_mid=1.0, ivr=35.0)
    now = datetime(2099, 12, 1, tzinfo=timezone.utc)
    rules = load_exit_rules("short_put_spread_index_45d")
    metrics = compute_position_metrics(
        position,
        now=now,
        provider=provider,
        vol_snapshot={"XSP": 0.25},
        exit_rules=rules,
    )

    assert metrics.current_mid == pytest.approx(1.0)
    assert metrics.unrealized_pl_total == pytest.approx(50.0)
    assert metrics.max_profit_total == pytest.approx(150.0)
    assert metrics.max_loss_total == pytest.approx(350.0)
    assert metrics.pnl_pct_of_max_profit == pytest.approx(50.0 / 150.0)
    assert metrics.dte and metrics.dte > 30
    assert metrics.ivr == pytest.approx(35.0)
    assert metrics.strategy_family == "credit_spread"

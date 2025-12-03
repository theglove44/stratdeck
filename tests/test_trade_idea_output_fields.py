import json
import os
from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner

from stratdeck.agents.trade_planner import TradePlanner
from stratdeck import cli as cli_module
from stratdeck.strategy_engine import SymbolStrategyTask
from stratdeck.strategies import (
    DTERule,
    DeltaBand,
    DeltaRule,
    ProductType,
    StrategyFilters,
    StrategyTemplate,
    UniverseConfig,
    UniverseSource,
    UniverseSourceType,
    WidthRule,
    WidthRuleType,
)
from stratdeck.tools.dates import compute_dte
import stratdeck.tools.chain_pricing_adapter as chains_adapter
import stratdeck.tools.chains as chains_module


class StubChainsWithDeltas:
    expiry = "2025-01-17"
    dte_val = 30

    def get_expiration_candidates(self, symbol: str):
        return [
            {
                "expiration-date": self.expiry,
                "days-to-expiration": self.dte_val,
                "is_monthly": True,
            }
        ]

    def build_vertical_by_delta(
        self,
        symbol: str,
        option_type: str,
        width: float,
        target_delta: float,
        delta_band=None,
        expiry=None,
        dte_target=None,
    ):
        short_delta = 0.26 if option_type == "put" else 0.28
        long_delta = 0.05
        return {
            "credit": 1.5,
            "credit_per_width": 0.3,
            "pop": 0.65,
            "width": width,
            "legs": [
                {
                    "side": "short",
                    "type": option_type,
                    "strike": 100.0 if option_type == "put" else 105.0,
                    "expiry": self.expiry,
                    "quantity": 1,
                    "delta": short_delta,
                    "dte": self.dte_val,
                },
                {
                    "side": "long",
                    "type": option_type,
                    "strike": 95.0 if option_type == "put" else 110.0,
                    "expiry": self.expiry,
                    "quantity": 1,
                    "delta": long_delta,
                    "dte": self.dte_val,
                },
            ],
            "expiry": self.expiry,
            "dte": self.dte_val,
            "short_delta": short_delta,
        }

    def build_iron_condor_by_delta(
        self,
        symbol: str,
        width: float,
        target_delta: float,
        delta_band=None,
        expiry=None,
        dte_target=None,
    ):
        put_side = self.build_vertical_by_delta(
            symbol=symbol,
            option_type="put",
            width=width,
            target_delta=target_delta,
            delta_band=delta_band,
            expiry=expiry,
            dte_target=dte_target,
        )
        call_side = self.build_vertical_by_delta(
            symbol=symbol,
            option_type="call",
            width=width,
            target_delta=target_delta,
            delta_band=delta_band,
            expiry=expiry,
            dte_target=dte_target,
        )
        return {
            "credit": put_side["credit"] + call_side["credit"],
            "credit_per_width": 0.3,
            "pop": 0.6,
            "width": width,
            "legs": (put_side["legs"] or []) + (call_side["legs"] or []),
            "expiry": self.expiry,
            "dte": self.dte_val,
            "short_put_delta": put_side["short_delta"],
            "short_call_delta": call_side["short_delta"],
            "position_delta": put_side["short_delta"] - call_side["short_delta"],
        }


def _scan_row(symbol: str, low: float, high: float, ivr: float):
    return {
        "symbol": symbol,
        "ivr": ivr,
        "ta_directional_bias": "bullish",
        "ta_vol_bias": "normal",
        "strategy_hint": "short_premium_range",
        "ta": {
            "scores": {
                "directional_bias": "bullish",
                "vol_bias": "normal",
                "ta_bias": 0.0,
            },
            "structure": {
                "support": [low],
                "resistance": [high],
                "range": {
                    "low": low,
                    "high": high,
                    "in_range": True,
                    "position_in_range": 0.5,
                },
            },
            "trend_regime": {"state": "uptrend"},
            "vol_regime": {"state": "normal"},
        },
    }


def _task(option_type: str) -> SymbolStrategyTask:
    source = UniverseSource(type=UniverseSourceType.STATIC, tickers=["SPX"])
    universe = UniverseConfig(
        name="index_core",
        product_type=ProductType.INDEX,
        source=source,
    )
    strategy = StrategyTemplate(
        name="short_put_spread_index_45d" if option_type != "both" else "iron_condor_index_45d",
        applies_to_universes=["index_core"],
        product_type=ProductType.INDEX,
        option_type=option_type,
        dte=DTERule(target=45, min=20, max=50),
        delta=DeltaRule(short_leg=DeltaBand(target=0.3, min=0.25, max=0.35)),
        width_rule=WidthRule(type=WidthRuleType.FIXED, allowed=[5], default=5),
        filters=StrategyFilters(min_ivr=0.2, min_credit_per_width=0.25, min_pop=0.55),
    )
    return SymbolStrategyTask(symbol="SPX", strategy=strategy, universe=universe)


def _equity_task(symbol: str) -> SymbolStrategyTask:
    source = UniverseSource(type=UniverseSourceType.STATIC, tickers=[symbol])
    universe = UniverseConfig(
        name="equity_core",
        product_type=ProductType.EQUITY,
        source=source,
    )
    strategy = StrategyTemplate(
        name="short_put_spread_equity_45d",
        applies_to_universes=["equity_core"],
        product_type=ProductType.EQUITY,
        option_type="put",
        dte=DTERule(target=45, min=25, max=55),
        delta=DeltaRule(short_leg=DeltaBand(target=0.25, min=0.2, max=0.3)),
        width_rule=WidthRule(type=WidthRuleType.FIXED, allowed=[5], default=5),
        filters=StrategyFilters(min_ivr=0.2, min_credit_per_width=0.1, min_pop=0.55),
    )
    return SymbolStrategyTask(symbol=symbol, strategy=strategy, universe=universe)


def _equity_chain_fixture(expiry: str) -> dict:
    return {
        "symbol": "AMZN",
        "expiry": expiry,
        "puts": [
            {"strike": 100.0, "bid": 0.5, "ask": 0.7, "mid": 0.6, "delta": -0.28, "greeks": {"delta": -0.28}},
            {"strike": 95.0, "bid": 0.2, "ask": 0.3, "mid": 0.25, "delta": -0.08, "greeks": {"delta": -0.08}},
        ],
        "calls": [],
    }


@pytest.fixture
def planner():
    return TradePlanner(chains_client=StubChainsWithDeltas())


def test_trade_idea_carries_dte_and_leg_delta(monkeypatch, planner):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    task = _task(option_type="put")

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[_scan_row("SPX", 100.0, 110.0, ivr=0.4)],
        tasks=[task],
        dte_target=45,
        max_per_symbol=1,
    )

    assert ideas and len(ideas) == 1
    idea = ideas[0]
    assert idea.dte == StubChainsWithDeltas.dte_val
    assert idea.expiry == StubChainsWithDeltas.expiry
    assert idea.spread_width == pytest.approx(5.0)

    short_legs = [leg for leg in idea.legs if leg.side == "short"]
    assert short_legs, "expected at least one short leg"
    assert all(leg.delta is not None for leg in short_legs)
    assert all(leg.dte == StubChainsWithDeltas.dte_val for leg in idea.legs)
    assert idea.short_put_delta == pytest.approx(0.26)


def test_short_and_long_leg_views_share_canonical_legs(monkeypatch, planner):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    task = _task(option_type="put")

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[_scan_row("SPX", 100.0, 110.0, ivr=0.4)],
        tasks=[task],
        dte_target=45,
        max_per_symbol=1,
    )

    assert ideas and len(ideas) == 1
    idea = ideas[0]

    assert idea.short_legs and idea.long_legs

    short_leg = idea.short_legs[0]
    long_leg = idea.long_legs[0]

    canonical_short = next(leg for leg in idea.legs if leg.side == "short")
    canonical_long = next(leg for leg in idea.legs if leg.side == "long")

    assert short_leg is canonical_short
    assert long_leg is canonical_long
    assert short_leg.delta is not None and long_leg.delta is not None
    assert short_leg.dte == canonical_short.dte == StubChainsWithDeltas.dte_val
    assert long_leg.dte == canonical_long.dte == StubChainsWithDeltas.dte_val


def test_iron_condor_carries_both_short_leg_deltas(monkeypatch, planner):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    task = _task(option_type="both")

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[_scan_row("SPX", 100.0, 110.0, ivr=0.4)],
        tasks=[task],
        dte_target=45,
        max_per_symbol=1,
    )

    assert ideas and len(ideas) == 1
    idea = ideas[0]
    short_put = next((leg for leg in idea.legs if leg.side == "short" and leg.type == "put"), None)
    short_call = next((leg for leg in idea.legs if leg.side == "short" and leg.type == "call"), None)

    assert short_put is not None and short_call is not None
    assert short_put.delta is not None
    assert short_call.delta is not None
    assert idea.short_put_delta == pytest.approx(0.26)
    assert idea.short_call_delta == pytest.approx(0.28)
    assert idea.dte == StubChainsWithDeltas.dte_val
    assert idea.spread_width == pytest.approx(5.0)


def test_pricing_backfills_leg_deltas_from_live_chain(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    symbol = "AMZN"
    expiry = (datetime.utcnow().date() + timedelta(days=30)).isoformat()
    chain = {
        "symbol": symbol,
        "expiry": expiry,
        "puts": [
            {"strike": 100.0, "bid": 0.5, "ask": 0.7, "delta": -0.24, "greeks": {"delta": -0.24}},
            {"strike": 95.0, "bid": 0.2, "ask": 0.3, "delta": -0.08, "greeks": {"delta": -0.08}},
        ],
        "calls": [],
    }

    monkeypatch.setattr(
        chains_adapter,
        "get_chain",
        lambda sym, expiry=None: chain,
    )

    planner = TradePlanner()
    monkeypatch.setattr(
        planner.chains_client,
        "get_expiration_candidates",
        lambda s: [{"expiration-date": expiry, "days-to-expiration": 30, "is_monthly": True}],
    )
    monkeypatch.setattr(planner.chains_client, "build_vertical_by_delta", lambda *args, **kwargs: None)

    task = _equity_task(symbol)

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[_scan_row(symbol, 100.0, 120.0, ivr=0.35)],
        tasks=[task],
        dte_target=30,
        max_per_symbol=1,
    )

    assert ideas and len(ideas) == 1
    idea = ideas[0]
    short_put = next((leg for leg in idea.legs if leg.side == "short" and leg.type == "put"), None)
    long_put = next((leg for leg in idea.legs if leg.side == "long" and leg.type == "put"), None)

    assert short_put is not None and long_put is not None
    assert short_put.delta is not None
    assert long_put.delta is not None
    assert short_put.delta == pytest.approx(0.24)
    assert long_put.delta == pytest.approx(0.08)
    assert idea.dte == 30
    assert idea.spread_width == pytest.approx(5.0)


def test_equity_leg_delta_backfilled_from_chain(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    expiry = (datetime.utcnow().date() + timedelta(days=45)).isoformat()
    chain = _equity_chain_fixture(expiry)

    monkeypatch.setattr(chains_adapter, "get_chain", lambda sym, expiry=None: chain)
    monkeypatch.setattr(chains_module, "get_chain", lambda sym, expiry=None: chain)

    planner = TradePlanner()
    monkeypatch.setattr(
        planner.chains_client,
        "get_expiration_candidates",
        lambda s: [{"expiration-date": expiry, "days-to-expiration": compute_dte(expiry), "is_monthly": True}],
    )
    monkeypatch.setattr(
        planner.chains_client,
        "price_structure",
        lambda *args, **kwargs: None,
    )

    def _vertical_stub(
        symbol: str,
        option_type: str,
        width: float,
        target_delta: float,
        delta_band=None,
        expiry=None,
        dte_target=None,
    ):
        expiry_final = expiry or chain.get("expiry")
        return {
            "credit": 1.5,
            "credit_per_width": 0.3,
            "pop": 0.65,
            "width": width,
            "legs": [
                {"side": "short", "type": option_type, "strike": 100.0, "expiry": expiry_final, "quantity": 1},
                {"side": "long", "type": option_type, "strike": 95.0, "expiry": expiry_final, "quantity": 1},
            ],
            "expiry": expiry_final,
            "dte": compute_dte(expiry_final),
            # intentionally omit deltas to force backfill from chain lookup
        }

    monkeypatch.setattr(planner.chains_client, "build_vertical_by_delta", _vertical_stub)

    task = _equity_task("AMZN")

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=[_scan_row("AMZN", 100.0, 120.0, ivr=0.35)],
        tasks=[task],
        dte_target=45,
        max_per_symbol=1,
    )

    assert ideas and len(ideas) == 1
    idea = ideas[0]
    short_put = next((leg for leg in idea.legs if leg.side == "short" and leg.type == "put"), None)
    assert short_put is not None
    assert short_put.delta == pytest.approx(0.28)
    assert idea.short_put_delta == pytest.approx(0.28)
    assert idea.dte == compute_dte(expiry)
    assert idea.spread_width == pytest.approx(5.0)


def test_trade_ideas_cli_equity_includes_delta(monkeypatch):
    monkeypatch.setenv("STRATDECK_DATA_MODE", "mock")
    expiry = (datetime.utcnow().date() + timedelta(days=45)).isoformat()
    chain = _equity_chain_fixture(expiry)

    monkeypatch.setattr(chains_adapter, "get_chain", lambda sym, expiry=None: chain)
    monkeypatch.setattr(chains_module, "get_chain", lambda sym, expiry=None: chain)
    monkeypatch.setattr("stratdeck.cli.get_watchlist_symbols", lambda name: ["AMZN"])
    monkeypatch.setattr(
        chains_adapter.ChainPricingAdapter,
        "get_expiration_candidates",
        lambda self, symbol: [
            {"expiration-date": expiry, "days-to-expiration": compute_dte(expiry), "is_monthly": True}
        ],
    )

    def _vertical_stub(
        self,
        symbol: str,
        option_type: str,
        width: float,
        target_delta: float,
        delta_band=None,
        expiry=None,
        dte_target=None,
    ):
        expiry_final = expiry or chain.get("expiry")
        return {
            "credit": 1.5,
            "credit_per_width": 0.3,
            "pop": 0.65,
            "width": width,
            "legs": [
                {"side": "short", "type": option_type, "strike": 100.0, "expiry": expiry_final, "quantity": 1},
                {"side": "long", "type": option_type, "strike": 95.0, "expiry": expiry_final, "quantity": 1},
            ],
            "expiry": expiry_final,
            "dte": compute_dte(expiry_final),
        }

    monkeypatch.setattr(chains_adapter.ChainPricingAdapter, "build_vertical_by_delta", _vertical_stub)
    monkeypatch.setattr(chains_adapter.ChainPricingAdapter, "price_structure", lambda *args, **kwargs: None)

    def _fake_build_trade_ideas_for_tasks(tasks, strategy_hint, dte_target, max_per_symbol):
        planner = TradePlanner()
        return planner.generate_from_scan_results_with_strategies(
            scan_rows=[_scan_row("AMZN", 100.0, 120.0, ivr=0.35)],
            tasks=tasks,
            dte_target=dte_target,
            max_per_symbol=max_per_symbol,
        )

    monkeypatch.setattr(cli_module, "_build_trade_ideas_for_tasks", _fake_build_trade_ideas_for_tasks)

    runner = CliRunner()
    env = os.environ.copy()
    env["STRATDECK_DATA_MODE"] = "mock"

    result = runner.invoke(
        cli_module.cli,
        [
            "trade-ideas",
            "--universe",
            "tasty_watchlist_chris_historical_trades",
            "--strategy",
            "short_put_spread_equity_45d",
            "--max-per-symbol",
            "1",
            "--json-output",
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output

    lines = result.output.splitlines()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip() == "[")
        end_idx = len(lines) - 1 - next(i for i, line in enumerate(reversed(lines)) if line.strip() == "]")
    except StopIteration:
        assert False, result.output

    payload = json.loads("\n".join(lines[start_idx : end_idx + 1]))
    assert isinstance(payload, list) and payload
    idea = payload[0]
    assert idea.get("dte") is not None
    assert idea.get("spread_width") == pytest.approx(5.0)
    short_legs = [leg for leg in idea.get("legs", []) if leg.get("side") == "short"]
    assert short_legs and short_legs[0].get("delta") is not None

from stratdeck.agents.trade_planner import TradePlanner
from stratdeck.strategy_engine import build_strategy_universe_assignments, build_symbol_strategy_tasks
from stratdeck.strategies import load_strategy_config


def test_trade_idea_provenance_includes_strategy_context():
    cfg = load_strategy_config()
    assignments = build_strategy_universe_assignments(
        cfg=cfg,
        tasty_watchlist_resolver=lambda name, max_symbols: [],
    )
    assignments = [
        a
        for a in assignments
        if a.universe.name == "index_core" and a.strategy.name == "short_put_spread_index_45d"
    ]
    tasks = [
        t for t in build_symbol_strategy_tasks(assignments) if t.symbol == "SPX"
    ]

    planner = TradePlanner(chains_client=None, pricing_client=None)
    scan_rows = [
        {
            "symbol": "SPX",
            "ta": {
                "scores": {
                    "directional_bias": "neutral",
                    "vol_bias": "normal",
                    "ta_bias": 0.12,
                },
                "structure": {
                    "support": [4000.0],
                    "resistance": [4200.0],
                    "range": {"low": 4000.0, "high": 4200.0},
                },
                "trend_regime": {"state": "choppy_trend"},
                "vol_regime": {"state": "normal"},
            },
            "ta_directional_bias": "neutral",
            "ta_vol_bias": "normal",
            "ivr": 0.35,
        }
    ]

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=scan_rows,
        tasks=tasks,
        dte_target=45,
        max_per_symbol=1,
    )

    assert ideas, "expected trade idea generation to succeed"
    idea_dict = ideas[0].to_dict()
    provenance = idea_dict.get("provenance")

    assert provenance, f"missing provenance in {idea_dict}"
    assert provenance["strategy_template_name"] == "short_put_spread_index_45d"
    assert provenance["universe_name"] == "index_core"

    dte_rule = provenance.get("dte_rule_used") or {}
    assert dte_rule.get("selected") == 45
    assert dte_rule.get("rule", {}).get("target") == 45

    width_rule = provenance.get("width_rule_used") or {}
    assert width_rule.get("rule", {}).get("type") == "index_allowed"
    assert width_rule.get("selected") == 5

    filters = provenance.get("filters_applied") or {}
    assert filters.get("min_ivr") == 0.20
    candidate_values = filters.get("candidate_values") or {}
    assert candidate_values.get("ivr") == 0.35

from datetime import datetime, timezone

from stratdeck.tools.position_monitor import ExitDecision, ExitRulesConfig, PositionMetrics, evaluate_exit_rules


def _base_metrics(**kwargs):
    defaults = dict(
        position_id="1",
        symbol="XSP",
        trade_symbol="XSP",
        strategy_id="short_put_spread_index_45d",
        universe_id="index_core",
        underlying_price=100.0,
        entry_mid=1.0,
        current_mid=0.5,
        unrealized_pl_per_contract=50.0,
        unrealized_pl_total=50.0,
        max_profit_per_contract=100.0,
        max_profit_total=100.0,
        max_loss_per_contract=400.0,
        max_loss_total=400.0,
        pnl_pct_of_max_profit=0.5,
        pnl_pct_of_max_loss=0.125,
        expiry=None,
        dte=30.0,
        as_of=datetime(2099, 1, 1, tzinfo=timezone.utc),
        iv=None,
        ivr=30.0,
        is_short_premium=True,
        strategy_family="credit_spread",
    )
    defaults.update(kwargs)
    return PositionMetrics(**defaults)


def _rules(**kwargs):
    defaults = dict(
        strategy_family="credit_spread",
        is_short_premium=True,
        profit_target_basis="credit",
        profit_target_pct=0.5,
        dte_exit=21,
        ivr_soft_exit_below=20.0,
    )
    defaults.update(kwargs)
    return ExitRulesConfig(**defaults)


def test_exit_rules_profit_target_triggers_exit():
    metrics = _base_metrics(unrealized_pl_total=60.0, max_profit_total=100.0, pnl_pct_of_max_profit=0.6)
    decision = evaluate_exit_rules(metrics, _rules())
    assert isinstance(decision, ExitDecision)
    assert decision.action == "exit"
    assert decision.reason == "TARGET_PROFIT_HIT"


def test_exit_rules_dte_backstop_triggers_exit():
    metrics = _base_metrics(unrealized_pl_total=20.0, max_profit_total=100.0, pnl_pct_of_max_profit=0.2, dte=15.0)
    decision = evaluate_exit_rules(metrics, _rules())
    assert decision.action == "exit"
    assert decision.reason == "DTE_BELOW_THRESHOLD"


def test_exit_rules_ivr_soft_exit_marks_reason_only():
    metrics = _base_metrics(unrealized_pl_total=20.0, max_profit_total=100.0, pnl_pct_of_max_profit=0.2, ivr=15.0)
    decision = evaluate_exit_rules(metrics, _rules())
    assert decision.action == "hold"
    assert decision.reason == "IVR_BELOW_SOFT_EXIT"
    assert any("IVR" in msg for msg in decision.triggered_rules)

# stratdeck/cli.py
import click
import json
from datetime import datetime, timezone
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from .tools import orders
from .orchestrator import Orchestrator, OrchestratorConfig
from .agents.scout import ScoutAgent
from .agents.trader import TraderAgent
from .agents.risk import RiskAgent
from .agents.compliance import ComplianceAgent
from .agents.trade_planner import TradePlanner
from .core.config import cfg
from .strategies import load_strategy_config
from .strategy_engine import (
    build_strategy_universe_assignments,
    debug_print_assignments,
    SymbolStrategyTask,
    build_symbol_strategy_tasks,
)
from .tools.account import (
    is_live_mode,
    provider_account_summary,
    provider_positions_state,
)
from .data.factory import get_provider
from .tools.chartist import ChartistAgent
from .tools.scan_cache import (
    attach_ivr_to_scan_rows,
    store_scan_rows,
    store_trade_ideas,
)
from .tools.ta import load_last_scan
from .tools.ideas import load_last_ideas, persist_last_ideas
from .tools.positions import POS_PATH, PaperPosition, PositionsStore
from .tools.position_monitor import compute_position_metrics, evaluate_exit_rules, load_exit_rules
from .tools.vol import load_snapshot

LAST_TRADE_IDEAS_PATH = Path(".stratdeck/last_trade_ideas.json")
LAST_POSITION_MONITORING_PATH = Path(".stratdeck/last_position_monitoring.json")


def _fmt_row(c: dict) -> str:
    # accepts POP/IVR as 0-1 or 0-100 and renders nicely
    pop = c.get("pop", 0)
    ivr = c.get("ivr", 0)
    pop_pct = int(pop * 100) if pop <= 1 else int(pop)
    ivr_pct = int(ivr * 100) if ivr <= 1 else int(ivr)
    return (
        f"{c['symbol']:>5}  {c['strategy']:<10}  DTE {c['dte']:<3}  "
        f"width {c['width']:<2}  credit {c['credit']:<5}  "
        f"POP {pop_pct:>3}%  IVR {ivr_pct:>3}%  "
        f"score {c['score']:.3f}  {c['rationale']}"
    )


def _prepare_trader_agent() -> tuple[TraderAgent, dict]:
    positions_state = provider_positions_state() if is_live_mode() else {}
    compliance = ComplianceAgent.from_config(cfg(), positions_state=positions_state)
    if is_live_mode():
        acct = provider_account_summary()
        bp = acct.get("buying_power") if isinstance(acct, dict) else None
        if bp is not None:
            try:
                compliance.pack.account_bp_available = float(bp)
            except (TypeError, ValueError):
                pass
    trader = TraderAgent(compliance=compliance)
    portfolio = {
        "mode": "live" if is_live_mode() else "mock",
        "positions_state": positions_state,
    }
    return trader, portfolio


def _resolve_tasty_watchlist(name: str, max_symbols: Optional[int]) -> List[str]:
    """
    Adapter between config 'tasty_watchlist' universes and your existing
    Tastytrade watchlist API. For now this can be a stub.

    Once ready, replace the body with a real call using your Tasty SDK,
    e.g. tasty.get_watchlist(name).
    """
    # TODO: wire to real tastywatchlist helper.
    # Example skeleton:
    #
    # from stratdeck.tools.tasty import get_watchlist_symbols
    # symbols = get_watchlist_symbols(name)
    # if max_symbols is not None:
    #     symbols = symbols[:max_symbols]
    # return symbols
    #
    # For now, to keep things safe and testable, just:
    return []


def _build_trade_ideas_for_symbols(
    symbols: List[str],
    strategy_hint: str,
    dte_target: int,
    max_per_symbol: int,
) -> List[Any]:
    scout = ScoutAgent()
    scout.C["watchlist"] = symbols
    chartist = ChartistAgent()
    planner = TradePlanner()

    base_results = scout.run()
    if not base_results:
        click.echo("Scout returned no candidates.", err=True)
        return []

    enriched = chartist.analyze_scout_batch(
        scout_results=base_results,
        default_strategy_hint=strategy_hint,
    )
    if not enriched:
        click.echo("Chartist did not produce any TA-enriched rows.", err=True)
        return []

    ideas = planner.generate_from_scan_results(
        scan_rows=enriched,
        default_strategy=strategy_hint,
        dte_target=dte_target,
        max_per_symbol=max_per_symbol,
    )
    if not ideas:
        click.echo("No trade ideas matched the current signals.", err=True)
        return []

    return ideas


def _build_trade_ideas_for_tasks(
    tasks: List[SymbolStrategyTask],
    strategy_hint: str,
    dte_target: int,
    max_per_symbol: int,
) -> List[Any]:
    """
    Strategy-aware version of the trade-ideas pipeline.

    Works directly on (symbol, strategy, universe) tasks, still using
    Scout/Chartist for TA and funneling the enriched rows into the
    strategy-aware TradePlanner path.
    """
    if not tasks:
        click.echo("No tasks provided to trade-ideas engine.", err=True)
        return []

    scout = ScoutAgent()
    scout.C["watchlist"] = sorted({task.symbol for task in tasks})
    chartist = ChartistAgent()
    planner = TradePlanner()

    base_results = scout.run()
    if not base_results:
        click.echo("Scout returned no candidates.", err=True)
        return []

    enriched = chartist.analyze_scout_batch(
        scout_results=base_results,
        default_strategy_hint=strategy_hint,
    )
    if not enriched:
        click.echo("Chartist did not produce any TA-enriched rows.", err=True)
        return []

    iv_snapshot_path = Path("iv_snapshot.json")
    if iv_snapshot_path.exists():
        try:
            iv_snapshot = json.loads(iv_snapshot_path.read_text())
        except Exception as exc:
            click.echo(f"[warn] Failed to load iv_snapshot.json: {exc}", err=True)
            iv_snapshot = {}
    else:
        iv_snapshot = {}

    scan_rows = attach_ivr_to_scan_rows(enriched, iv_snapshot)

    strategy_context: Dict[str, SymbolStrategyTask] = {
        task.symbol.upper(): task for task in tasks
    }

    for row in scan_rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        task = strategy_context.get(symbol.upper())
        if not task:
            continue
        row["strategy_assignment"] = {
            "strategy_template_name": task.strategy.name,
            "strategy_template_label": task.strategy.label,
        }

    store_scan_rows(scan_rows)

    ideas = planner.generate_from_scan_results_with_strategies(
        scan_rows=scan_rows,
        tasks=tasks,
        dte_target=dte_target,
        max_per_symbol=max_per_symbol,
    )
    if not ideas:
        click.echo("No trade ideas matched the current signals.", err=True)
        return []

    return ideas


@click.group()
def cli():
    """StratDeck Agent System CLI"""
    pass


@cli.command()
@click.option(
    "--top", default=5, show_default=True, help="How many candidates to display"
)
def scan(top: int):
    """Scan watchlist and print ranked candidates."""
    agent = ScoutAgent()
    results = agent.run()
    store_scan_rows(results or [])
    if not results:
        print("No candidates passed thresholds.")
        return
    for i, r in enumerate(results[:top], start=1):
        print(f"{i}. {_fmt_row(r)}")


@cli.command()
@click.option("--pick", type=int, help="Index from the latest scan output (1-based)")
@click.option("--qty", type=int, default=1, show_default=True)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    show_default=True,
    help="Simulate a paper fill and persist position",
)
@click.option(
    "--live-order",
    is_flag=True,
    help="Attempt tastytrade preview/place when in live mode",
)
def enter(pick: Optional[int], qty: int, confirm: bool, live_order: bool):
    """
    Build an order plan for the chosen candidate and run compliance.
    If --pick is omitted or cache is empty, performs a fresh scan and picks the top idea.
    Use --confirm to simulate a paper fill and journal it.
    """
    cache = load_last_scan()
    rows = cache.rows
    if not rows:
        agent = ScoutAgent()
        rows = agent.run() or []
        store_scan_rows(rows)
        if not rows:
            print("No candidates available to enter.")
            return

    if pick is None:
        pick = 1
    idx = pick - 1
    if idx < 0 or idx >= len(rows):
        print(f"Invalid pick {pick}. Run `scan` and choose 1..{len(rows)}.")
        return

    chosen = rows[idx]
    trader, portfolio = _prepare_trader_agent()
    if live_order and not is_live_mode():
        print("--live-order ignored: STRATDECK_DATA_MODE is not live.")
        live_order = False
    result = trader.enter_trade(
        chosen, qty, portfolio, confirm=confirm, live_order=live_order
    )

    comp = result["compliance"]
    plan = result["order_plan"]
    sp = plan["spread_plan"]

    print("Compliance:", "APPROVED" if comp["allowed"] else "REJECTED")
    if not comp["allowed"]:
        for r in comp["reasons"]:
            print(f" - {r}")

    print("\nOrderPlan:")
    print(f"  Symbol:   {sp['symbol']}  Expiry: {sp.get('expiry','')}")
    print(f"  Strategy: {sp['strategy']}")
    print(f"  Width:    {sp['width']}  Credit: {plan['price']}")
    print(f"  Qty:      {plan['qty']}  TIF:    {plan['tif']}")
    print(f"  Est BP Impact: {plan['est_bp_impact']}")
    print(f"  Max Loss:      {plan['max_loss']}  Fees: {plan['fees']}")

    if result.get("fill"):
        print(
            f"\nPaper Fill: {result['fill']['status']}  Ticket: {result['fill']['position_id']}"
        )
        if result.get("position_id"):
            print(f"Ledger Position ID: {result['position_id']}")
        print("Journal: OPEN entry written.")
    else:
        print("\n(Execution is stubbed; pass --confirm to simulate paper fill.)")
    if result.get("broker_preview"):
        print("\nBroker Preview (tastytrade):")
        print(result["broker_preview"])
    if result.get("broker_order"):
        print("Broker Order Response:")
        print(result["broker_order"])
    if result.get("broker_error"):
        print(f"Broker Error: {result['broker_error']}")


@cli.group(name="positions", invoke_without_command=True)
@click.pass_context
def positions(ctx: click.Context) -> None:
    """Paper positions utilities."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(positions_list)


@positions.command("list")
@click.option("--all", "include_all", is_flag=True, help="Include closed positions.")
@click.option("--json-output", is_flag=True, help="Emit JSON instead of human-readable text.")
def positions_list(include_all: bool, json_output: bool) -> None:
    store = PositionsStore(POS_PATH)
    pos_list = store.list_positions(status=None if include_all else "open")
    if not pos_list:
        click.echo("No positions found.")
        return
    if json_output:
        payload = [p.model_dump(mode="json") for p in pos_list]
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo("ID                                   Symbol  Strategy                       Qty  Status  Entry mid")
    click.echo("-" * 90)
    for pos in pos_list:
        entry_mid = f"{pos.entry_mid:.2f}" if pos.entry_mid is not None else "-"
        strategy_label = pos.strategy_id or pos.strategy or "-"
        status_label = (pos.status or "open")
        click.echo(
            f"{pos.id:<36}  {pos.symbol:<6}  {strategy_label:<30}  "
            f"{pos.qty:>3}  {status_label:<6}  {entry_mid}"
        )


def _monitor_snapshot(
    open_positions: List[PaperPosition],
    provider: Any,
    snapshot: Dict[str, float],
    now: datetime,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for pos in open_positions:
        rules = load_exit_rules(pos.strategy_id or pos.strategy or "")
        metrics = compute_position_metrics(
            pos,
            now=now,
            provider=provider,
            vol_snapshot=snapshot,
            exit_rules=rules,
        )
        decision = evaluate_exit_rules(metrics, rules)
        items.append(
            {
                "position": pos.model_dump(mode="json"),
                "metrics": metrics.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            }
        )
    return items


@positions.command("monitor")
@click.option("--json-output", is_flag=True, help="Emit JSON instead of human-readable text.")
def positions_monitor(json_output: bool) -> None:
    store = PositionsStore(POS_PATH)
    open_positions = store.get_open_positions()
    if not open_positions:
        click.echo("No open positions found.")
        return

    provider = get_provider()
    snapshot = load_snapshot()
    now = datetime.now(timezone.utc)

    items = _monitor_snapshot(open_positions, provider, snapshot, now)

    LAST_POSITION_MONITORING_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_POSITION_MONITORING_PATH.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")

    if json_output:
        click.echo(json.dumps(items, indent=2, default=str))
        return

    click.echo("ID                                   Symbol  Strategy                       DTE   P/L($)   %MaxP  IVR   Action  Reason")
    click.echo("-" * 110)
    for item in items:
        pos = item["position"]
        metrics = item["metrics"]
        decision = item["decision"]
        dte_val = metrics.get("dte")
        pnl_total = metrics.get("unrealized_pl_total")
        pct_mp = metrics.get("pnl_pct_of_max_profit")
        ivr_val = metrics.get("ivr")
        dte_label = f"{float(dte_val):.1f}" if dte_val is not None else "-"
        pnl_label = f"{pnl_total:.2f}" if pnl_total is not None else "-"
        pct_label = f"{pct_mp * 100:.0f}%" if pct_mp not in (None, 0) else "-"
        ivr_label = f"{ivr_val:.1f}" if ivr_val is not None else "-"
        click.echo(
            f"{pos.get('id',''):<36}  {pos.get('symbol',''):<6}  {pos.get('strategy_id') or pos.get('strategy') or '-':<30}  "
            f"{dte_label:>4}  "
            f"{pnl_label:>7}  "
            f"{pct_label:>5}  "
            f"{ivr_label:>5}  "
            f"{decision.get('action','').upper():<6}  {decision.get('reason','-')}"
        )


@positions.command("close-auto")
@click.option("--dry-run", is_flag=True, help="Evaluate exits without persisting any changes.")
@click.option("--json-output", is_flag=True, help="Emit JSON instead of human-readable text.")
def positions_close_auto(dry_run: bool, json_output: bool) -> None:
    store = PositionsStore(POS_PATH)
    open_positions = store.get_open_positions()
    if not open_positions:
        click.echo("No open positions found.")
        return

    provider = get_provider()
    snapshot = load_snapshot()
    now = datetime.now(timezone.utc)

    items = _monitor_snapshot(open_positions, provider, snapshot, now)
    closed: List[Dict[str, Any]] = []

    for idx, pos in enumerate(open_positions):
        decision = items[idx]["decision"]
        metrics = items[idx]["metrics"]
        if (decision.get("action") or "").lower() != "exit":
            continue
        updated = PaperPosition.model_validate(pos.model_dump())
        updated.status = "closed"
        updated.closed_at = now
        updated.exit_mid = float(metrics.get("current_mid") or updated.entry_mid or 0.0)
        updated.realized_pl_total = metrics.get("unrealized_pl_total")
        updated.exit_reason = decision.get("reason") or updated.exit_reason or "auto"
        if updated.max_profit_total is None and metrics.get("max_profit_total") is not None:
            updated.max_profit_total = metrics.get("max_profit_total")
        if updated.max_loss_total is None and metrics.get("max_loss_total") is not None:
            updated.max_loss_total = metrics.get("max_loss_total")
        closed.append(
            {
                "position_before": pos.model_dump(mode="json"),
                "position_after": updated.model_dump(mode="json"),
                "metrics": metrics,
                "decision": decision,
            }
        )
        if not dry_run:
            store.update_position(updated)

    if json_output:
        click.echo(json.dumps(closed, indent=2, default=str))
        return

    if not closed:
        click.echo("No positions met exit rules.")
        return

    click.echo("Closed positions:")
    for item in closed:
        after = item["position_after"]
        metrics = item["metrics"]
        click.echo(
            f"- {after.get('id')} {after.get('symbol')} {after.get('strategy_id') or after.get('strategy')} "
            f"exit_mid={metrics.get('current_mid')} pnl={metrics.get('unrealized_pl_total')} reason={item['decision'].get('reason')}"
            + (" (dry-run)" if dry_run else "")
        )


@positions.command("close")
@click.option("--id", "position_id", required=True, help="Position id/UUID to close.")
@click.option("--reason", type=str, default=None, help="Optional reason to record on close.")
@click.option("--dry-run", is_flag=True, help="Show close details without persisting.")
@click.option("--json-output", is_flag=True, help="Emit JSON instead of human-readable text.")
def positions_close(position_id: str, reason: Optional[str], dry_run: bool, json_output: bool) -> None:
    store = PositionsStore(POS_PATH)
    pos = store.get(position_id)
    if pos is None:
        raise click.ClickException(f"Position {position_id} not found.")

    provider = get_provider()
    snapshot = load_snapshot()
    now = datetime.now(timezone.utc)

    rules = load_exit_rules(pos.strategy_id or pos.strategy or "")
    metrics = compute_position_metrics(
        pos,
        now=now,
        provider=provider,
        vol_snapshot=snapshot,
        exit_rules=rules,
    )
    decision = evaluate_exit_rules(metrics, rules)

    planned_exit_mid = metrics.current_mid
    realized_pl = metrics.unrealized_pl_total

    before_dump = pos.model_dump(mode="json")
    if not dry_run:
        pos.status = "closed"
        pos.closed_at = now
        pos.exit_mid = planned_exit_mid
        pos.realized_pl_total = realized_pl
        pos.exit_reason = reason or decision.reason or "manual"
        if pos.max_profit_total is None and metrics.max_profit_total is not None:
            pos.max_profit_total = metrics.max_profit_total
        if pos.max_loss_total is None and metrics.max_loss_total is not None:
            pos.max_loss_total = metrics.max_loss_total
        store.update_position(pos)
    after_dump = pos.model_dump(mode="json")

    payload = {
        "position_before": before_dump,
        "position_after": after_dump,
        "metrics": metrics.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "planned_exit_mid": planned_exit_mid,
        "planned_realized_pl": realized_pl,
        "dry_run": dry_run,
    }
    if json_output:
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        pnl_label = f"{realized_pl:.2f}" if realized_pl is not None else "0.00"
        click.echo(
            f"Position {pos.id} {pos.symbol} {pos.strategy_id or pos.strategy} "
            f"exit_mid={planned_exit_mid:.2f} "
            f"pnl={pnl_label} "
            f"reason={reason or decision.reason}"
        )

@positions.command("show")
@click.option("--id", "position_id", required=True, help="Position id/UUID to show.")
@click.option("--json-output", is_flag=True, help="Emit JSON instead of human-readable text.")
def positions_show(position_id: str, json_output: bool) -> None:
    store = PositionsStore(POS_PATH)
    pos = store.get(position_id)
    if pos is None:
        raise click.ClickException(f"Position {position_id} not found.")

    if json_output:
        click.echo(json.dumps(pos.model_dump(mode="json"), indent=2, default=str))
        return

    click.echo(f"ID: {pos.id}")
    click.echo(f"Symbol: {pos.symbol}")
    click.echo(f"Trade symbol: {pos.trade_symbol or '-'}")
    click.echo(f"Strategy: {pos.strategy_id or pos.strategy or '-'}")
    click.echo(f"Direction: {pos.direction or '-'}")
    click.echo(f"Qty: {pos.qty}")
    click.echo(f"Entry mid: {pos.entry_mid}")
    click.echo(f"Entry notional: {pos.entry_total}")
    click.echo(f"Status: {pos.status}")
    click.echo(f"Opened at: {pos.opened_at}")
    if pos.legs:
        click.echo("Legs:")
        for leg in pos.legs:
            click.echo(
                f"  - {leg.side or '?'} {leg.type or '?'} {leg.strike} exp {leg.expiry} qty {leg.quantity} mid {leg.entry_mid}"
            )


@cli.command()
def monitor():
    from .agents.risk import RiskAgent

    risk = RiskAgent()
    for rec in risk.check_positions():
        print(rec)


@cli.command("strategy-universes")
def strategy_universes_cmd():
    """
    Dump Strategy × Universe assignments from strategies.yaml.

    This is a debug helper to verify config wiring before we
    plug it into the full trade-ideas engine.
    """
    # For now we don't wire tasty here; static & local_file only.
    debug_print_assignments()


@cli.command()
@click.option("--daily", is_flag=True, help="Shortcut for --days 1")
@click.option(
    "--days", type=int, default=1, show_default=True, help="Lookback window for report"
)
def report(daily: bool, days: int):
    """Generate performance summary for the recent period."""
    from .tools import reports as report_tools

    window = 1 if daily else max(1, days)
    summary = report_tools.summarize_daily(window)
    print(f"Daily Report (last {window} day(s))")
    print("-" * 40)
    print(f"Opened: {summary['opened']}  Closed: {summary['closed']}")
    print(
        f"Wins: {summary['wins']}  Losses: {summary['losses']}  Win%: {summary['win_rate']:.1f}%"
    )
    print(f"Realized PnL: ${summary['realized_pnl']:.2f}")
    print(
        f"Open Positions: {summary['open_positions']}  Closed Positions: {summary['closed_positions']}"
    )
    acct = summary.get("live_account") or {}
    if acct:
        bp = acct.get("buying_power")
        cash = acct.get("cash")
        equity = acct.get("equity")
        print("Live Account Snapshot:")
        if bp is not None:
            print(f"  Buying Power: ${float(bp):.2f}")
        if cash is not None:
            print(f"  Cash: ${float(cash):.2f}")
        if equity is not None:
            print(f"  Equity: ${float(equity):.2f}")


@cli.command()
@click.option(
    "--position-id", type=str, required=True, help="Position ID/UUID from positions store"
)
@click.option(
    "--exit-credit",
    type=float,
    required=True,
    help="Net credit/debit received when closing (positive debit to close)",
)
@click.option("--note", type=str, default="", help="Optional note for the journal")
def close(position_id: int, exit_credit: float, note: str):
    """Close a paper position and log realized P/L."""
    from .tools.positions import close_position
    from .agents.journal import JournalAgent

    try:
        result = close_position(position_id, exit_credit)
    except ValueError as exc:
        click.echo(str(exc))
        raise SystemExit(1)

    pnl = result["pnl"]
    symbol = result.get("symbol")
    JournalAgent().log_close(
        position_id,
        symbol or "",
        pnl,
        note or "CLOSE",
        {
            "exit_credit": exit_credit,
            "qty": result.get("qty"),
        },
    )
    click.echo(f"Position {position_id} closed for P/L ${pnl:.2f}")


@cli.command()
def doctor():
    """Run StratDeck diagnostics."""
    import os
    from .core.config import cfg, scoring_conf

    problems = []
    live_mode = is_live_mode()

    here = os.path.dirname(__file__)
    expected = ["agents", "core", "tools", "conf", "data"]
    for d in expected:
        if not os.path.isdir(os.path.join(here, d)):
            problems.append(f"Missing folder: stratdeck/{d}")

    must = [
        ("core/config.py", "config loader"),
        ("core/policies.py", "policy pack"),
        ("core/scoring.py", "scoring"),
        ("tools/vol.py", "IVR snapshot loader"),
        ("tools/orders.py", "order preview"),
        ("agents/scout.py", "scout agent"),
        ("agents/trader.py", "trader agent"),
    ]
    for rel, desc in must:
        if not os.path.exists(os.path.join(here, rel)):
            problems.append(f"Missing file: stratdeck/{rel} ({desc})")

    try:
        _ = cfg()
        _ = scoring_conf()
    except Exception as e:
        problems.append(f"Config loading failed: {e}")

    if live_mode:
        user = os.getenv("TASTY_USER") or os.getenv("TT_USERNAME")
        pw = os.getenv("TASTY_PASS") or os.getenv("TT_PASSWORD")
        if not user or not pw:
            problems.append(
                "Live mode enabled but TASTY_USER/TASTY_PASS (or TT_*) not set"
            )
        else:
            try:
                provider = get_provider()
                _ = provider_account_summary()
                provider.get_option_chain("SPX")
            except Exception as exc:
                problems.append(f"Tastytrade provider check failed: {exc}")

    if problems:
        print("Doctor found issues:")
        for p in problems:
            print(" -", p)
    else:
        msg = (
            "All green. Live mode ready."
            if live_mode
            else "All green. Ready to ruin some market makers' day (paper only)."
        )
        print(msg)


@cli.command()
@click.option(
    "--symbols",
    "-s",
    multiple=True,
    required=True,
    help="One or more symbols to analyse (e.g. -s SPX -s XSP).",
)
@click.option(
    "--strategy-hint",
    "-H",
    type=click.Choice(
        [
            "short_premium_range",
            "short_premium_trend",
            "long_premium_breakout",
        ],
        case_sensitive=False,
    ),
    default=None,
    help="Optional strategy context for the TA engine.",
)
@click.option(
    "--timeframe",
    "-t",
    default="30m",
    show_default=True,
    help="Primary timeframe to use for TA (for now, single TF passed to the engine).",
)
@click.option(
    "--lookback-bars",
    "-n",
    default=200,
    show_default=True,
    help="Number of bars to fetch for TA on the primary timeframe.",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="If set, prints the raw TA_RESULT dict as JSON instead of a human summary.",
)
def chartist(symbols, strategy_hint, timeframe, lookback_bars, json_output):
    """
    Run ChartistAgent over one or more symbols and print technical analysis output.

    Examples:
      python -m stratdeck.cli chartist -s SPX -s XSP -H short_premium_range
      python -m stratdeck.cli chartist -s SPY --json-output
    """
    # For now we run without an LLM client, so ChartistAgent will use its
    # built-in fallback_summary(). If you have a central LLM client, pass it in here.
    agent = ChartistAgent()

    click.echo(f"Running Chartist TA for symbols: {', '.join(symbols)}", err=True)
    if strategy_hint:
        click.echo(f"Strategy hint: {strategy_hint}", err=True)

    results = {}
    for sym in symbols:
        try:
            ta_res = agent.analyze_symbol(
                symbol=sym,
                strategy_hint=strategy_hint,
                timeframes=(timeframe,),
                lookback_bars=lookback_bars,
            )
        except Exception as exc:
            click.echo(f"[{sym}] ERROR: {exc}", err=True)
            continue

        if json_output:
            results[sym] = ta_res.to_dict()
        else:
            summary = agent._fallback_summary(ta_res)
            click.echo("\n" + "=" * 60)
            click.echo(summary)
            click.echo("=" * 60 + "\n")

    if json_output:
        # Dump a compact JSON blob that other tools / scripts can consume.
        click.echo(json.dumps(results, indent=2, default=str))


@cli.command(name="scan-ta")
@click.option(
    "--strategy-hint",
    "-H",
    type=click.Choice(
        [
            "short_premium_range",
            "short_premium_trend",
            "long_premium_breakout",
        ],
        case_sensitive=False,
    ),
    default="short_premium_range",
    show_default=True,
    help="Hint for how TA should weight signals (range, trend, breakout).",
)
@click.option(
    "--timeframe",
    "-t",
    default="30m",
    show_default=True,
    help="Primary timeframe to use for TA.",
)
@click.option(
    "--lookback-bars",
    "-n",
    default=200,
    show_default=True,
    help="Number of bars to fetch for TA on the primary timeframe.",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Output enriched scan as JSON instead of a human-readable table.",
)
def scan_ta(strategy_hint, timeframe, lookback_bars, json_output):
    """
    Run ScoutAgent → ChartistAgent pipeline.

    1) ScoutAgent.scan() generates candidate symbols.
    2) ChartistAgent attaches TA metadata (ta_score, directional bias, vol bias, levels).
    3) Output can be JSON or a simple table.

    Example:
      python -m stratdeck.cli scan-ta
      python -m stratdeck.cli scan-ta -H long_premium_breakout --json-output
    """
    scout = ScoutAgent()
    chartist = ChartistAgent()  # no LLM client yet; uses fallback summary

    # 1) Run scout
    base_results = scout.run()
    if not base_results:
        click.echo("Scout returned no candidates.", err=True)
        return

    # 2) Run chartist enrichment
    enriched = chartist.analyze_scout_batch(
        scout_results=base_results,
        default_strategy_hint=strategy_hint,
    )

    if json_output:
        # Dump the full enriched rows, including 'ta' blob
        click.echo(json.dumps(enriched, indent=2, default=str))
        return

    # 3) Human-readable summary table
    click.echo(f"Found {len(enriched)} candidates (scout → chartist):\n")

    # Simple header
    header = f"{'SYM':<8} {'SCOUT_SCORE':<12} {'TA_SCORE':<8} {'DIR':<18} {'VOL':<14}"
    click.echo(header)
    click.echo("-" * len(header))

    for row in enriched:
        sym = str(row.get("symbol", "??"))
        scout_score = row.get("score", "")
        ta_score = row.get("ta_score", 0.0)
        dir_bias = row.get("ta_directional_bias", "")
        vol_bias = row.get("ta_vol_bias", "")

        # format nice-ish
        scout_str = (
            f"{scout_score:.3f}"
            if isinstance(scout_score, (int, float))
            else str(scout_score)
        )
        ta_str = f"{ta_score:.2f}"

        line = f"{sym:<8} {scout_str:<12} {ta_str:<8} {dir_bias:<18} {vol_bias:<14}"
        click.echo(line)


@cli.command(name="trade-ideas")
@click.option(
    "--strategy-hint",
    "-H",
    type=click.Choice(
        [
            "short_premium_range",
            "short_premium_trend",
            "long_premium_breakout",
        ],
        case_sensitive=False,
    ),
    default="short_premium_range",
    show_default=True,
    help="Baseline strategy to assume when TA data lacks explicit hints.",
)
@click.option(
    "--dte-target",
    "-d",
    type=int,
    default=45,
    show_default=True,
    help="Target days-to-expiration used when constructing synthetic legs.",
)
@click.option(
    "--max-per-symbol",
    type=int,
    default=1,
    show_default=True,
    help="Maximum number of ideas per symbol.",
)
@click.option(
    "--universe",
    "universe_filters",
    multiple=True,
    help="Limit scan to specific universes (e.g. --universe index_core).",
)
@click.option(
    "--strategy",
    "strategy_filters",
    multiple=True,
    help="Limit scan to specific strategies (e.g. --strategy iron_condor_index_30d).",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Emit raw TradeIdea structures as JSON instead of formatted text.",
)
@click.argument(
    "output_path",
    required=False,
    type=click.Path(dir_okay=False, writable=True),
)
def trade_ideas(
    strategy_hint,
    dte_target,
    max_per_symbol,
    universe_filters: tuple[str, ...],
    strategy_filters: tuple[str, ...],
    json_output,
    output_path,
):
    """
    Generate structured trade ideas using Scout → Chartist → TradePlanner pipeline.
    """
    strategy_cfg = load_strategy_config()

    assignments = build_strategy_universe_assignments(
        cfg=strategy_cfg,
        tasty_watchlist_resolver=_resolve_tasty_watchlist,
    )

    if universe_filters:
        universe_filter_set = {u.strip() for u in universe_filters}
        assignments = [a for a in assignments if a.universe.name in universe_filter_set]

    if strategy_filters:
        strategy_filter_set = {s.strip() for s in strategy_filters}
        assignments = [a for a in assignments if a.strategy.name in strategy_filter_set]

    if not assignments:
        click.echo(
            "No matching strategy/universe assignments. Check filters/config.",
            err=True,
        )
        return

    tasks = build_symbol_strategy_tasks(assignments)
    if not tasks:
        click.echo("No tasks resolved from selected universes.", err=True)
        return

    symbols = sorted({t.symbol for t in tasks})

    scan_banner = (
        "[trade-ideas] Running scan for "
        f"{len(symbols)} symbols: "
        + ", ".join(symbols[:20])
        + (" ..." if len(symbols) > 20 else "")
    )
    if json_output:
        click.echo(scan_banner, err=True)
    else:
        click.echo(scan_banner)

    ideas = _build_trade_ideas_for_tasks(
        tasks=tasks,
        strategy_hint=strategy_hint,
        dte_target=dte_target,
        max_per_symbol=max_per_symbol,
    )
    if not ideas:
        return

    payload = [idea.to_dict() for idea in ideas]

    store_trade_ideas(ideas)
    persist_last_ideas(payload, path=LAST_TRADE_IDEAS_PATH)

    if output_path and not json_output:
        raise click.ClickException("--output-path requires --json-output.")

    if json_output:
        blob = json.dumps(payload, indent=2, default=str)
        if output_path:
            path = Path(output_path)
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(blob, encoding="utf-8")
            click.echo(f"Wrote {len(ideas)} ideas to {output_path}")
        else:
            click.echo(blob)
        return

    click.echo(f"Generated {len(ideas)} trade idea(s):\n")
    for idea in ideas:
        header = (
            f"{idea.symbol}: {idea.strategy} | {idea.direction} | "
            f"vol {idea.vol_context} | target {idea.dte_target or dte_target} DTE"
        )
        click.echo(header)
        click.echo(f"  Rationale: {idea.rationale}")
        if idea.notes:
            for note in idea.notes:
                click.echo(f"  Note: {note}")
        click.echo("  Legs:")
        for leg in idea.legs:
            expiry = leg.expiry or (f"{idea.dte_target or dte_target}DTE")
            strike = (
                f"{leg.strike:.2f}"
                if isinstance(leg.strike, (int, float))
                else str(leg.strike)
            )
            click.echo(
                f"    - {leg.side.upper()} {leg.type.upper()} "
                f"{strike} exp {expiry} x{leg.quantity}"
            )
        if idea.underlying_price_hint:
            click.echo(f"  Underlying hint: {idea.underlying_price_hint:.2f}")
        click.echo("")

    click.echo(
        "\nTip: re-run with --json-output to feed into TraderAgent or other tools."
    )


@cli.command("enter-auto")
@click.option(
    "--qty",
    type=int,
    default=1,
    show_default=True,
    help="Number of spreads/contracts to open.",
)
@click.option(
    "--confirm/--no-confirm",
    default=False,
    show_default=True,
    help="Ask for confirmation before placing the order.",
)
@click.option(
    "--live",
    "live_order",
    is_flag=True,
    help="Place the order in LIVE mode instead of paper.",
)
@click.option(
    "--index",
    "idea_index",
    type=int,
    default=0,
    show_default=True,
    help="Index of the idea from last_trade_ideas.json to enter.",
)
@click.option(
    "--json-output",
    is_flag=True,
    help="Emit the created PaperPosition as JSON instead of text.",
)
def enter_auto(qty: int, confirm: bool, live_order: bool, idea_index: int, json_output: bool) -> None:
    """
    Enter a paper position from the most recent trade ideas.
    """
    if live_order:
        click.echo("[enter-auto] live orders are not supported; defaulting to paper mode.")

    try:
        ideas = load_last_ideas(LAST_TRADE_IDEAS_PATH)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(f"Failed to load trade ideas from {LAST_TRADE_IDEAS_PATH}: {exc}") from exc

    if not ideas:
        raise click.ClickException(f"{LAST_TRADE_IDEAS_PATH} does not contain any trade ideas.")

    if idea_index < 0 or idea_index >= len(ideas):
        raise click.ClickException(f"Idea index {idea_index} is out of range for {len(ideas)} ideas.")

    idea = ideas[idea_index]
    idea_dict = (
        idea.to_dict()
        if hasattr(idea, "to_dict")
        else idea
        if isinstance(idea, dict)
        else getattr(idea, "__dict__", {}) or {}
    )
    summary_label = f"{idea_dict.get('trade_symbol') or idea_dict.get('symbol') or '?'} {idea_dict.get('strategy_id') or idea_dict.get('strategy') or ''}"

    if not confirm:
        proceed = click.confirm(f"Enter idea #{idea_index}: {summary_label} x{qty}?", default=False)
        if not proceed:
            click.echo("Aborted.")
            raise SystemExit(1)

    try:
        result = orders.enter_paper_trade(idea, qty=qty)
    except Exception as exc:  # pragma: no cover - defensive
        raise click.ClickException(f"Failed to enter paper trade: {exc}") from exc

    position = result.get("position")
    position_payload = (
        position.model_dump(mode="json") if hasattr(position, "model_dump") else position if isinstance(position, dict) else {}
    )

    if json_output:
        payload = position_payload or result
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    entry_mid = position_payload.get("entry_mid") or result.get("entry_mid_price")
    entry_total = position_payload.get("entry_total") or result.get("total_credit")

    click.echo("[enter-auto] Entered paper position:")
    click.echo(f"  id: {result.get('position_id') or position_payload.get('id')}")
    click.echo(f"  symbol: {result.get('symbol')}")
    click.echo(f"  strategy: {idea_dict.get('strategy_id') or idea_dict.get('strategy')}")
    click.echo(f"  qty: {qty}")
    if entry_mid is not None:
        side = "credit" if float(entry_mid) >= 0 else "debit"
        click.echo(f"  entry_mid: {float(entry_mid):.2f} {side}")
    if entry_total is not None:
        click.echo(f"  entry_notional: ${float(entry_total):.2f}")


@cli.command("enter-from-idea")
@click.option(
    "-i",
    "--index",
    "idea_index",
    type=int,
    required=True,
    help="Index into last TradeIdeas set (0-based)",
)
@click.option(
    "-q",
    "--qty",
    type=int,
    default=1,
    show_default=True,
    help="Number of spreads/contracts to trade",
)
@click.option(
    "--live/--paper",
    "live",
    default=False,
    show_default=True,
    help="Route to live broker (True) or keep in paper mode (False)",
)
@click.option(
    "--confirm/--no-confirm",
    default=False,
    show_default=True,
    help="If set, actually place order(s) instead of preview-only",
)
def enter_from_idea(idea_index: int, qty: int, live: bool, confirm: bool) -> None:
    """
    Enter a trade directly from the last TradeIdeas run.

    Usage:
      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json
      python -m stratdeck.cli enter-from-idea -i 0
    """
    ideas = load_last_ideas()
    if not ideas:
        raise click.ClickException(
            "No TradeIdeas found. Run 'trade-ideas --json-output' first."
        )

    try:
        idea = ideas[idea_index]
    except IndexError:
        raise click.ClickException(
            f"Idea index {idea_index} out of range; have {len(ideas)} ideas."
        )

    trader, portfolio = _prepare_trader_agent()
    result = trader.enter_from_idea(
        idea=idea,
        qty=qty,
        portfolio=portfolio,
        confirm=confirm,
        live_order=live,
    )

    click.echo(json.dumps(result, indent=2, default=str))


@cli.command("vet-idea")
@click.option(
    "-i",
    "--index",
    "idea_index",
    type=int,
    required=True,
    help="Index into last TradeIdeas set (0-based)",
)
@click.option(
    "-q",
    "--qty",
    type=int,
    default=1,
    show_default=True,
    help="Quantity of spreads/contracts to vet",
)
def vet_idea(idea_index: int, qty: int) -> None:
    """
    Preview compliance outcome for a saved TradeIdea without placing anything.
    """
    ideas = load_last_ideas()
    if not ideas:
        raise click.ClickException(
            "No TradeIdeas found. Run 'trade-ideas --json-output' first."
        )

    try:
        idea = ideas[idea_index]
    except IndexError:
        raise click.ClickException(
            f"Idea index {idea_index} out of range; have {len(ideas)} ideas."
        )

    trader, _ = _prepare_trader_agent()
    report = trader.vet_idea(idea=idea, qty=qty)
    click.echo(json.dumps(report, indent=2, default=str))


@cli.command("ideas-vet")
@click.option(
    "-q",
    "--qty",
    type=int,
    default=1,
    show_default=True,
    help="Qty to use for compliance/buying power checks",
)
@click.option(
    "--json-output",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Optional path to write a JSON vetting report",
)
def ideas_vet(qty: int, json_output: Optional[str]) -> None:
    """
    Run all TradeIdeas from the last trade-ideas run through ComplianceAgent.

    Example:
      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json
      python -m stratdeck.cli ideas-vet
    """
    ideas = load_last_ideas()
    if not ideas:
        raise click.ClickException(
            "No TradeIdeas found. Run 'trade-ideas --json-output' first."
        )

    trader, _ = _prepare_trader_agent()
    report = []

    for idx, idea in enumerate(ideas):
        symbol = (
            getattr(idea, "symbol", None)
            or getattr(idea, "underlying", None)
            or (idea.get("symbol") if isinstance(idea, dict) else None)
        )
        try:
            res = trader.vet_idea(idea, qty=qty)
            report.append(
                {
                    "index": idx,
                    "symbol": symbol,
                    "allowed": res["allowed"],
                    "violations": res.get("violations", []),
                    "price": res["order_summary"].get("price"),
                    "est_bp_impact": res["order_summary"].get("est_bp_impact"),
                    "spread_plan": res["spread_plan"],
                }
            )
        except Exception as exc:
            report.append(
                {
                    "index": idx,
                    "symbol": symbol,
                    "allowed": False,
                    "violations": [f"error: {exc}"],
                    "price": None,
                    "est_bp_impact": None,
                    "spread_plan": None,
                }
            )

    if json_output:
        path = Path(json_output)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        click.echo(f"Wrote vetting report for {len(report)} ideas to {json_output}")
        return

    click.echo(f"Vetting {len(report)} ideas (qty={qty}):")
    click.echo("")
    click.echo(" idx  symbol   allowed   est_bp   reason")
    click.echo("---- -------- --------- -------- -----------------------------------")
    for row in report:
        status = "OK" if row["allowed"] else "VETO"
        reason = "; ".join(row["violations"]) if row["violations"] else ""
        bp = row["est_bp_impact"]
        bp_str = f"{bp:.2f}" if isinstance(bp, (int, float)) else "-"
        sym = (row["symbol"] or "")[:8]
        click.echo(f"{row['index']:>3}  {sym:<8} {status:<9} {bp_str:<8} {reason[:60]}")


@cli.command("auto")
@click.option(
    "--live/--paper",
    default=False,
    help="Route orders to live broker (default is paper-only).",
)
@click.option(
    "--max-trades",
    type=int,
    default=1,
    help="Maximum number of auto trades allowed per day (journal-based).",
)
@click.option(
    "--min-pop",
    type=float,
    default=0.50,
    help="Minimum probability of profit (0–1) for candidate selection.",
)
@click.option(
    "--min-credit-per-width",
    type=float,
    default=0.30,
    help="Minimum credit/width ratio for candidate selection.",
)
@click.option(
    "--qty",
    type=int,
    default=1,
    help="Contract quantity per trade.",
)
@click.option(
    "--idea-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".stratdeck/last_trade_ideas.json"),
    show_default=True,
    help="Path where trade-ideas will write the ideas JSON.",
)
@click.option(
    "--journal-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(".stratdeck/auto_journal.csv"),
    show_default=True,
    help="Path to the auto-trade journal CSV.",
)
@click.option(
    "--dry-run/--execute",
    default=False,
    help="If enabled, vet and rank only; do NOT call enter_from_idea.",
)
def auto(
    live: bool,
    max_trades: int,
    min_pop: float,
    min_credit_per_width: float,
    qty: int,
    idea_json: Path,
    journal_path: Path,
    dry_run: bool,
) -> None:
    """
    Run a single auto-trading orchestration cycle.

    - Regenerates ideas via `trade-ideas`.
    - Vets and filters candidates using TraderAgent + Compliance.
    - Ranks candidates and (optionally) enters a paper or live trade.
    - Prints a JSON OrchestratorResult summary to stdout.
    """
    logger = logging.getLogger("stratdeck.auto")

    config = OrchestratorConfig(
        max_trades_per_day=max_trades,
        min_pop=min_pop,
        min_credit_per_width=min_credit_per_width,
        default_qty=qty,
        idea_json_path=idea_json,
        journal_path=journal_path,
        live=live,
        dry_run=dry_run,
    )

    # Instantiate TraderAgent the same way you do elsewhere in CLI.
    trader = TraderAgent()

    orch = Orchestrator(trader=trader, config=config, logger=logger)
    result = orch.run_once()

    # JSON summary – ready for piping into logs or other tooling.
    payload = result.to_dict()
    click.echo(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    cli()

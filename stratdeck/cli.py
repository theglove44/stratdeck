# stratdeck/cli.py
import click
import json
from pathlib import Path
from typing import Optional
from .agents.scout import ScoutAgent
from .agents.trader import TraderAgent
from .agents.risk import RiskAgent
from .agents.compliance import ComplianceAgent
from .agents.trade_planner import TradePlanner
from .core.config import cfg
from .tools.account import is_live_mode, provider_account_summary, provider_positions_state
from .data.factory import get_provider
from .tools.chartist import ChartistAgent
from .tools.scan_cache import store_scan_rows, store_trade_ideas
from .tools.ta import load_last_scan

def _fmt_row(c: dict) -> str:
    # accepts POP/IVR as 0-1 or 0-100 and renders nicely
    pop = c.get("pop", 0)
    ivr = c.get("ivr", 0)
    pop_pct = int(pop * 100) if pop <= 1 else int(pop)
    ivr_pct = int(ivr * 100) if ivr <= 1 else int(ivr)
    return (f"{c['symbol']:>5}  {c['strategy']:<10}  DTE {c['dte']:<3}  "
            f"width {c['width']:<2}  credit {c['credit']:<5}  "
            f"POP {pop_pct:>3}%  IVR {ivr_pct:>3}%  "
            f"score {c['score']:.3f}  {c['rationale']}")


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
    portfolio = {"mode": "live" if is_live_mode() else "mock", "positions_state": positions_state}
    return trader, portfolio

@click.group()
def cli():
    """StratDeck Agent System CLI"""
    pass

@cli.command()
@click.option("--top", default=5, show_default=True, help="How many candidates to display")
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
@click.option("--confirm", is_flag=True, default=False, show_default=True, help="Simulate a paper fill and persist position")
@click.option("--live-order", is_flag=True, help="Attempt tastytrade preview/place when in live mode")
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
    result = trader.enter_trade(chosen, qty, portfolio, confirm=confirm, live_order=live_order)

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
        print(f"\nPaper Fill: {result['fill']['status']}  Ticket: {result['fill']['position_id']}")
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

@cli.command()
def positions():
    from .tools.positions import list_positions
    rows = list_positions()
    if not rows:
        print("No open positions.")
        return
    for r in rows:
        line = (f"#{r['id']} {r['symbol']} {r['strategy']} width {r['width']} "
                f"credit {r['credit']} qty {r['qty']} status {r['status']}")
        if r.get("exit_credit"):
            line += f" exit {r['exit_credit']}"
        if r.get("pnl"):
            line += f" pnl {r['pnl']}"
        print(line)

@cli.command()
def monitor():
    from .agents.risk import RiskAgent
    risk = RiskAgent()
    for rec in risk.check_positions():
        print(rec)

@cli.command()
@click.option("--daily", is_flag=True, help="Shortcut for --days 1")
@click.option("--days", type=int, default=1, show_default=True, help="Lookback window for report")
def report(daily: bool, days: int):
    """Generate performance summary for the recent period."""
    from .tools import reports as report_tools

    window = 1 if daily else max(1, days)
    summary = report_tools.summarize_daily(window)
    print(f"Daily Report (last {window} day(s))")
    print("-" * 40)
    print(f"Opened: {summary['opened']}  Closed: {summary['closed']}")
    print(f"Wins: {summary['wins']}  Losses: {summary['losses']}  Win%: {summary['win_rate']:.1f}%")
    print(f"Realized PnL: ${summary['realized_pnl']:.2f}")
    print(f"Open Positions: {summary['open_positions']}  Closed Positions: {summary['closed_positions']}")
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
@click.option("--position-id", type=int, required=True, help="Position ID from positions.csv")
@click.option("--exit-credit", type=float, required=True, help="Net credit/debit received when closing (positive debit to close)")
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
    JournalAgent().log_close(position_id, symbol or "", pnl, note or "CLOSE", {
        "exit_credit": exit_credit,
        "qty": result.get("qty"),
    })
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
        _ = cfg(); _ = scoring_conf()
    except Exception as e:
        problems.append(f"Config loading failed: {e}")

    if live_mode:
        user = os.getenv("TASTY_USER") or os.getenv("TT_USERNAME")
        pw = os.getenv("TASTY_PASS") or os.getenv("TT_PASSWORD")
        if not user or not pw:
            problems.append("Live mode enabled but TASTY_USER/TASTY_PASS (or TT_*) not set")
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
        msg = "All green. Live mode ready." if live_mode else "All green. Ready to ruin some market makers' day (paper only)."
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
        scout_str = f"{scout_score:.3f}" if isinstance(scout_score, (int, float)) else str(scout_score)
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
    "--json-output",
    is_flag=True,
    help="Emit raw TradeIdea structures as JSON instead of formatted text.",
)
@click.argument(
    "output_path",
    required=False,
    type=click.Path(dir_okay=False, writable=True),
)
def trade_ideas(strategy_hint, dte_target, max_per_symbol, json_output, output_path):
    """
    Generate structured trade ideas using Scout → Chartist → TradePlanner pipeline.
    """
    scout = ScoutAgent()
    chartist = ChartistAgent()
    planner = TradePlanner()

    base_results = scout.run()
    if not base_results:
        click.echo("Scout returned no candidates.", err=True)
        return

    enriched = chartist.analyze_scout_batch(
        scout_results=base_results,
        default_strategy_hint=strategy_hint,
    )
    if not enriched:
        click.echo("Chartist did not produce any TA-enriched rows.", err=True)
        return

    ideas = planner.generate_from_scan_results(
        scan_rows=enriched,
        default_strategy=strategy_hint,
        dte_target=dte_target,
        max_per_symbol=max_per_symbol,
    )
    if not ideas:
        click.echo("No trade ideas matched the current signals.", err=True)
        return

    store_trade_ideas(ideas)

    if output_path and not json_output:
        raise click.ClickException("--output-path requires --json-output.")

    if json_output:
        payload = [idea.to_dict() for idea in ideas]
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
            strike = f"{leg.strike:.2f}" if isinstance(leg.strike, (int, float)) else str(leg.strike)
            click.echo(
                f"    - {leg.side.upper()} {leg.type.upper()} "
                f"{strike} exp {expiry} x{leg.quantity}"
            )
        if idea.underlying_price_hint:
            click.echo(f"  Underlying hint: {idea.underlying_price_hint:.2f}")
        click.echo("")

    click.echo("\nTip: re-run with --json-output to feed into TraderAgent or other tools.")


@cli.command("enter-from-idea")
@click.option("-i", "--index", "idea_index", type=int, required=True, help="Index into last TradeIdea list")
@click.option("-q", "--qty", type=int, default=1, show_default=True, help="Number of spreads/contracts")
@click.option("--live/--paper", default=False, show_default=True, help="Route via real broker or stay in paper mode")
@click.option(
    "--confirm/--no-confirm",
    default=False,
    show_default=True,
    help="If set, actually place (paper + broker) instead of preview only",
)
def enter_from_idea(idea_index: int, qty: int, live: bool, confirm: bool) -> None:
    """
    Enter a trade directly from a ranked TradeIdea.

    Flow:
      - read last scan
      - pick idea[index]
      - snap strikes/expiry via chains engine
      - run compliance + preview
      - optionally place order(s)
    """
    cache = load_last_scan()
    ideas = cache.ideas
    if not ideas:
        raise click.ClickException("No TradeIdeas found in last scan.")

    try:
        idea = ideas[idea_index]
    except IndexError:
        raise click.ClickException(f"Idea index {idea_index} out of range; have {len(ideas)} ideas.")

    trader, portfolio = _prepare_trader_agent()
    result = trader.enter_from_idea(
        idea=idea,
        qty=qty,
        portfolio=portfolio,
        confirm=confirm,
        live_order=live,
    )

    click.echo(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    cli()

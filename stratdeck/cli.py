# stratdeck/cli.py
import click
from typing import Optional
from .agents.scout import ScoutAgent
from .agents.trader import TraderAgent
from .agents.risk import RiskAgent
from .agents.compliance import ComplianceAgent
from .core.config import cfg
from .tools.account import is_live_mode, provider_account_summary, provider_positions_state
from .data.factory import get_provider

# cache the most recent scan so you can pick by index
_last_scan: list[dict] = []

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

@click.group()
def cli():
    """StratDeck Agent System CLI"""
    pass

@cli.command()
@click.option("--top", default=5, show_default=True, help="How many candidates to display")
def scan(top: int):
    """Scan watchlist and print ranked candidates."""
    global _last_scan
    agent = ScoutAgent()
    results = agent.run()
    _last_scan = results or []
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
    global _last_scan

    if not _last_scan:
        agent = ScoutAgent()
        _last_scan = agent.run()
        if not _last_scan:
            print("No candidates available to enter.")
            return

    if pick is None:
        pick = 1
    idx = pick - 1
    if idx < 0 or idx >= len(_last_scan):
        print(f"Invalid pick {pick}. Run `scan` and choose 1..{len(_last_scan)}.")
        return

    chosen = _last_scan[idx]
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
    if live_order and not is_live_mode():
        print("--live-order ignored: STRATDECK_DATA_MODE is not live.")
        live_order = False
    portfolio = {"mode": "live" if is_live_mode() else "mock", "positions_state": positions_state}
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


if __name__ == "__main__":
    cli()

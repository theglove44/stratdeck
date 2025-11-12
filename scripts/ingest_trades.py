#!/usr/bin/env python3
"""Ingest Tastytrade CSV exports into the StratDeck SQLite database."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger("ingest_trades")
DEFAULT_SOURCE = Path.home() / "Documents" / "Tasty Trades"
DEFAULT_DB = Path.cwd() / "stratdeck.db"
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages" / "data" / "src" / "schema.sql"
DATE_PATTERNS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
TIME_PATTERNS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")


@dataclass
class ParsedTrade:
    source: Path
    rownum: int
    timestamp: datetime
    underlying: str
    expiration: str
    call_put: str
    strike: float
    quantity: int
    price: float
    action: str  # BUY or SELL
    intent: str  # OPEN or CLOSE
    side: str  # LONG or SHORT orientation of position
    order_id: str
    trade_id: str
    fees: float


@dataclass
class FillRecord:
    id: str
    leg_id: str
    ts: datetime
    action: str
    price: float
    qty: int
    fees: float


@dataclass
class LegRecord:
    id: str
    strategy_id: str
    side: str
    call_put: str
    strike: float
    expiration: str
    open_quantity: int = 0
    remaining_quantity: int = 0
    total_signed_premium: float = 0.0
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


@dataclass
class StrategyRecord:
    id: str
    underlying: str
    strategy_type: str
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    legs: Dict[str, LegRecord] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Tastytrade CSV trades into SQLite")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Directory containing *.csv exports (default: ~/Documents/Tasty Trades)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite database path (default: ./stratdeck.db or STRATDECK_DB)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing strategies/legs/fills before ingesting",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log without writing to the database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")


def discover_csv_files(directory: Path) -> List[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"source directory not found: {directory}")
    return sorted(p for p in directory.glob("*.csv") if p.is_file())


def read_schema_statements(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8")
    statements: List[str] = []
    current: List[str] = []
    for line in raw.splitlines():
        if line.strip().startswith("--"):
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip())
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return [stmt for stmt in statements if stmt]


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(" ", "")


def parse_occ_symbol(symbol: str) -> Tuple[str, str, str, float]:
    cleaned = normalize_symbol(symbol)
    if len(cleaned) < 16:
        raise ValueError(f"symbol does not look like OCC format: {symbol}")
    idx = None
    for pos in range(len(cleaned) - 6):
        segment = cleaned[pos:pos + 6]
        if segment.isdigit():
            idx = pos
            break
    if idx is None or idx == 0:
        raise ValueError(f"could not locate expiration digits in symbol: {symbol}")
    root = cleaned[:idx]
    rest = cleaned[idx:]
    if len(rest) < 7:
        raise ValueError(f"malformed OCC symbol: {symbol}")
    exp = rest[:6]
    cp_flag = rest[6]
    strike_raw = rest[7:]
    expiration = datetime.strptime(exp, "%y%m%d").date().isoformat()
    strike = int(strike_raw) / 1000.0
    call_put = "CALL" if cp_flag.upper() == "C" else "PUT"
    return root, expiration, call_put, strike


def get_first(row: Dict[str, str], candidates: Sequence[str]) -> Optional[str]:
    lower = {k.lower(): v for k, v in row.items()}
    for key in candidates:
        if key in row and row[key]:
            return row[key]
        value = lower.get(key.lower())
        if value:
            return value
    return None


def parse_quantity(value: str) -> int:
    stripped = value.replace(",", "").strip()
    if not stripped:
        return 0
    return abs(int(float(stripped)))


def parse_float(value: Optional[str]) -> float:
    if value is None:
        return 0.0
    stripped = value.replace(",", "").strip()
    if stripped in {"", "-"}:
        return 0.0
    return float(stripped)


def parse_timestamp(row: Dict[str, str]) -> datetime:
    date_str = get_first(row, ["trade_date", "tradedate", "date", "execdate", "executiondate"])
    if not date_str:
        raise ValueError("missing trade date")
    time_str = get_first(row, ["trade_time", "tradetime", "time", "exectime", "executiontime"])
    date_obj: Optional[datetime] = None
    for pattern in DATE_PATTERNS:
        try:
            date_obj = datetime.strptime(date_str.strip(), pattern)
            break
        except ValueError:
            continue
    if date_obj is None:
        raise ValueError(f"unrecognised date format: {date_str}")
    if time_str:
        time_obj: Optional[datetime] = None
        for pattern in TIME_PATTERNS:
            try:
                time_obj = datetime.strptime(time_str.strip(), pattern)
                break
            except ValueError:
                continue
        if time_obj is None:
            raise ValueError(f"unrecognised time format: {time_str}")
        return datetime.combine(date_obj.date(), time_obj.time())
    return date_obj


def parse_action(action_raw: str) -> Tuple[str, str]:
    lower = action_raw.lower()
    if "to open" in lower:
        intent = "OPEN"
    elif "to close" in lower:
        intent = "CLOSE"
    else:
        intent = "OPEN" if "buy" in lower else "CLOSE"
    if "sell" in lower:
        action = "SELL"
    elif "buy" in lower:
        action = "BUY"
    else:
        raise ValueError(f"cannot determine buy/sell from action '{action_raw}'")
    return action, intent


def determine_side(action: str, intent: str) -> str:
    if intent == "OPEN":
        return "SHORT" if action == "SELL" else "LONG"
    return "SHORT" if action == "BUY" else "LONG"


def iter_trades_from_csv(path: Path) -> Iterator[ParsedTrade]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader, start=2):  # header is line 1
            try:
                symbol = get_first(row, ["symbol", "option_symbol", "optionsymbol"])
                if not symbol:
                    continue
                instrument = get_first(row, ["instrumenttype", "instrument_type"])
                if instrument and "option" not in instrument.lower():
                    continue
                action_raw = get_first(row, ["action", "transactiontype", "description"])
                if not action_raw:
                    LOGGER.debug("skip row %s (no action): %s", idx, row)
                    continue
                action, intent = parse_action(action_raw)
                underlying, expiration, call_put, strike = parse_occ_symbol(symbol)
                quantity_raw = get_first(row, ["quantity", "qty"])
                if not quantity_raw:
                    continue
                quantity = parse_quantity(quantity_raw)
                if quantity == 0:
                    continue
                price_str = get_first(row, ["price", "netprice", "executionprice"])
                price = parse_float(price_str)
                fees = (
                    parse_float(get_first(row, ["commission"]))
                    + parse_float(get_first(row, ["fees", "clearingfees"]))
                    + parse_float(get_first(row, ["secfee", "sec_fee"]))
                    + parse_float(get_first(row, ["otherfee", "other_fee"]))
                )
                order_id = get_first(row, ["orderid", "order_id", "ordernumber"]) or f"order-{path.stem}-{idx}"
                trade_id = get_first(row, ["tradeid", "trade_id", "executionid"]) or f"fill-{order_id}-{idx}"
                timestamp = parse_timestamp(row)
                side = determine_side(action, intent)
                yield ParsedTrade(
                    source=path,
                    rownum=idx,
                    timestamp=timestamp,
                    underlying=underlying,
                    expiration=expiration,
                    call_put=call_put,
                    strike=strike,
                    quantity=quantity,
                    price=price,
                    action=action,
                    intent=intent,
                    side=side,
                    order_id=str(order_id),
                    trade_id=str(trade_id),
                    fees=fees,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Skipping row %s in %s: %s", idx, path.name, exc)


def load_trades(paths: Iterable[Path]) -> List[ParsedTrade]:
    trades: List[ParsedTrade] = []
    for path in paths:
        LOGGER.info("Loading %s", path.name)
        trades.extend(iter_trades_from_csv(path))
    trades.sort(key=lambda t: t.timestamp)
    return trades


def choose_strategy_type(trades: Sequence[ParsedTrade]) -> str:
    call_put_counts = Counter(t.call_put for t in trades)
    unique_sides = {(t.call_put, t.side) for t in trades}
    if call_put_counts["CALL"] and call_put_counts["PUT"]:
        if {("CALL", "SHORT"), ("CALL", "LONG"), ("PUT", "SHORT"), ("PUT", "LONG")} <= unique_sides:
            return "IC"
        return "Strangle"
    if call_put_counts["PUT"]:
        if {("PUT", "SHORT"), ("PUT", "LONG")} <= unique_sides:
            return "PCS"
        return "Strangle"
    if call_put_counts["CALL"]:
        if {("CALL", "SHORT"), ("CALL", "LONG")} <= unique_sides:
            return "CCS"
        return "Strangle"
    return "Unknown"


def allocate_strategy_id(base: str, existing: Dict[str, StrategyRecord]) -> str:
    if base not in existing:
        return base
    counter = 2
    while True:
        candidate = f"{base}_{counter:02d}"
        if candidate not in existing:
            return candidate
        counter += 1


def build_positions(trades: Sequence[ParsedTrade]) -> Tuple[Dict[str, StrategyRecord], List[FillRecord]]:
    orders: Dict[str, List[ParsedTrade]] = defaultdict(list)
    closing_trades: List[ParsedTrade] = []
    for trade in trades:
        if trade.intent == "OPEN":
            orders[trade.order_id].append(trade)
        else:
            closing_trades.append(trade)

    strategies: Dict[str, StrategyRecord] = {}
    fill_records: List[FillRecord] = []
    open_leg_lookup: Dict[Tuple[str, str, str, float, str], List[LegRecord]] = defaultdict(list)

    for order_id, group in sorted(orders.items(), key=lambda item: min(t.timestamp for t in item[1])):
        underlying = Counter(t.underlying for t in group).most_common(1)[0][0]
        expiration = Counter(t.expiration for t in group).most_common(1)[0][0]
        opened_at = min(t.timestamp for t in group)
        strategy_type = choose_strategy_type(group)
        base_id = f"{strategy_type}_{underlying}_{expiration}"
        strategy_id = allocate_strategy_id(base_id, strategies)
        strategy = StrategyRecord(
            id=strategy_id,
            underlying=underlying,
            strategy_type=strategy_type,
            status="OPEN",
            opened_at=opened_at,
        )
        strategies[strategy_id] = strategy

        for trade in group:
            leg_id = f"{strategy_id}:{trade.call_put}:{int(trade.strike * 1000):08d}:{trade.side}"
            leg = strategy.legs.get(leg_id)
            if leg is None:
                leg = LegRecord(
                    id=leg_id,
                    strategy_id=strategy_id,
                    side=trade.side,
                    call_put=trade.call_put,
                    strike=trade.strike,
                    expiration=trade.expiration,
                )
                strategy.legs[leg_id] = leg
                open_leg_lookup[(trade.underlying, trade.expiration, trade.call_put, trade.strike, trade.side)].append(leg)
            leg.open_quantity += trade.quantity
            leg.remaining_quantity += trade.quantity
            leg.total_signed_premium += (trade.price if trade.side == "SHORT" else -trade.price) * trade.quantity
            if leg.opened_at is None or trade.timestamp < leg.opened_at:
                leg.opened_at = trade.timestamp
            fill_records.append(
                FillRecord(
                    id=trade.trade_id,
                    leg_id=leg.id,
                    ts=trade.timestamp,
                    action=trade.action,
                    price=trade.price,
                    qty=trade.quantity,
                    fees=trade.fees,
                )
            )

    for trade in closing_trades:
        key = (trade.underlying, trade.expiration, trade.call_put, trade.strike, trade.side)
        legs = open_leg_lookup.get(key)
        if not legs:
            LOGGER.warning(
                "No open position found for close fill %s (%s %s %s %.2f)",
                trade.trade_id,
                trade.underlying,
                trade.expiration,
                trade.call_put,
                trade.strike,
            )
            continue
        leg = next((candidate for candidate in legs if candidate.remaining_quantity > 0), None)
        if leg is None:
            LOGGER.warning("All legs already closed for %s", trade.trade_id)
            continue
        if trade.quantity > leg.remaining_quantity:
            LOGGER.warning(
                "Close fill %s qty %s exceeds open balance %s; trimming",
                trade.trade_id,
                trade.quantity,
                leg.remaining_quantity,
            )
        qty = min(trade.quantity, leg.remaining_quantity)
        leg.remaining_quantity -= qty
        if leg.remaining_quantity == 0:
            leg.closed_at = trade.timestamp
        strategy = strategies.get(leg.strategy_id)
        if strategy is None:
            LOGGER.warning("Leg %s missing parent strategy for trade %s", leg.id, trade.trade_id)
        else:
            if all(l.remaining_quantity == 0 for l in strategy.legs.values()):
                strategy.status = "CLOSED"
                strategy.closed_at = trade.timestamp
            elif any(l.remaining_quantity != l.open_quantity for l in strategy.legs.values()):
                strategy.status = "ADJUSTED"
        fill_records.append(
            FillRecord(
                id=trade.trade_id,
                leg_id=leg.id,
                ts=trade.timestamp,
                action=trade.action,
                price=trade.price,
                qty=qty,
                fees=trade.fees,
            )
        )

    fill_records.sort(key=lambda record: record.ts)
    return strategies, fill_records


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"schema file not found: {SCHEMA_PATH}")
    for statement in read_schema_statements(SCHEMA_PATH):
        conn.execute(statement)
    conn.commit()


def reset_tables(conn: sqlite3.Connection) -> None:
    LOGGER.info("Clearing strategies, legs, and fills tables")
    conn.executescript(
        """
        DELETE FROM fills;
        DELETE FROM legs;
        DELETE FROM strategies;
        """
    )
    conn.commit()


def persist(conn: sqlite3.Connection, strategies: Dict[str, StrategyRecord], fills: List[FillRecord]) -> None:
    cur = conn.cursor()
    for strategy in strategies.values():
        cur.execute(
            """
            INSERT OR REPLACE INTO strategies (id, underlying, strategy_type, status, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                strategy.id,
                strategy.underlying,
                strategy.strategy_type,
                strategy.status,
                strategy.opened_at.isoformat(timespec="seconds"),
                strategy.closed_at.isoformat(timespec="seconds") if strategy.closed_at else None,
            ),
        )
        for leg in strategy.legs.values():
            avg_price = 0.0
            if leg.open_quantity:
                avg_price = round(leg.total_signed_premium / leg.open_quantity, 4)
            cur.execute(
                """
                INSERT OR REPLACE INTO legs (id, strategy_id, side, call_put, strike, expiration, qty, avg_price, opened_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    leg.id,
                    strategy.id,
                    leg.side,
                    leg.call_put,
                    leg.strike,
                    leg.expiration,
                    leg.open_quantity,
                    avg_price,
                    leg.opened_at.isoformat(timespec="seconds") if leg.opened_at else None,
                    leg.closed_at.isoformat(timespec="seconds") if leg.closed_at else None,
                ),
            )
    for fill in fills:
        cur.execute(
            """
            INSERT OR REPLACE INTO fills (id, leg_id, ts, action, price, qty, fees)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.id,
                fill.leg_id,
                fill.ts.isoformat(timespec="seconds"),
                fill.action,
                fill.price,
                fill.qty,
                fill.fees,
            ),
        )
    conn.commit()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    env_db = os.environ.get("STRATDECK_DB")
    db_path = args.db
    if env_db and args.db == DEFAULT_DB:
        db_path = Path(env_db)
    env_source = os.environ.get("STRATDECK_TRADES_DIR")
    source = args.source
    if env_source and args.source == DEFAULT_SOURCE:
        source = Path(env_source)
    LOGGER.info("Database: %s", db_path)
    csv_dir = source.expanduser()
    LOGGER.info("Scanning %s", csv_dir)
    try:
        files = discover_csv_files(csv_dir)
    except FileNotFoundError as exc:
        LOGGER.error(str(exc))
        return
    if not files:
        LOGGER.warning("No CSV files found in %s", csv_dir)
        return
    trades = load_trades(files)
    if not trades:
        LOGGER.warning("No ingestible trades detected")
        return
    strategies, fills = build_positions(trades)
    LOGGER.info(
        "Assembled %d strategies, %d legs, %d fills",
        len(strategies),
        sum(len(s.legs) for s in strategies.values()),
        len(fills),
    )
    if args.dry_run:
        LOGGER.info("Dry run complete; skipping database write")
        return
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        if args.reset:
            reset_tables(conn)
        persist(conn, strategies, fills)
    finally:
        conn.close()
    LOGGER.info("Ingestion complete")


if __name__ == "__main__":
    main()

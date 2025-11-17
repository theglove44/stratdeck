# stratdeck/strategy_engine.py

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from .strategies import (
    StrategyConfig,
    StrategyTemplate,
    UniverseConfig,
    UniverseSourceType,
    WidthRule,
    WidthRuleType,
    DTERule,
    load_strategy_config,
)


# ---------------------------------------------------------------------------
# Data structures for the strategy/universe layer
# ---------------------------------------------------------------------------

@dataclass
class StrategyUniverseAssignment:
    """
    Links a strategy template to a specific universe, with resolved tickers.
    This is the bridge object trade-ideas will iterate over.
    """

    strategy: StrategyTemplate
    universe: UniverseConfig
    symbols: List[str]


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------

def _load_local_file_tickers(path: str) -> List[str]:
    """
    Load tickers from a local file.
    Supported formats:
      - YAML/JSON with either:
          - a top-level list: ["SPY", "QQQ", ...]
          - or a dict with a 'tickers' key: { tickers: ["SPY", "QQQ"] }
    """
    p = Path(path)
    if not p.is_absolute():
        # resolve relative to project root or config dir if you prefer
        # For now we resolve relative to the file location that calls this.
        p = Path.cwd() / p

    if not p.exists():
        raise FileNotFoundError(f"Universe local_file path does not exist: {p}")

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []

    if isinstance(data, list):
        return [str(sym).upper() for sym in data]

    if isinstance(data, dict) and "tickers" in data:
        return [str(sym).upper() for sym in data["tickers"]]

    raise ValueError(
        f"Unsupported local_file format for universe tickers at {p}. "
        "Expected a list or a dict with 'tickers' key."
    )


def resolve_universe_tickers(
    universe: UniverseConfig,
    tasty_watchlist_resolver: Optional[Callable[[str, Optional[int]], List[str]]] = None,
) -> List[str]:
    """
    Turn a UniverseConfig into a concrete list of symbols.

    Args:
        universe: UniverseConfig instance.
        tasty_watchlist_resolver: Optional callback taking
            (watchlist_name, max_symbols) -> List[str]
            You will plug your real Tastytrade SDK helper here later.

    Returns:
        List of symbols (upper-cased).
    """
    src = universe.source

    if src.type == UniverseSourceType.STATIC:
        return [sym.upper() for sym in (src.tickers or [])]

    if src.type == UniverseSourceType.LOCAL_FILE:
        return _load_local_file_tickers(src.path or "")

    if src.type == UniverseSourceType.TASTY_WATCHLIST:
        if tasty_watchlist_resolver is None:
            raise RuntimeError(
                f"Universe '{universe.name}' uses tasty_watchlist source, "
                "but no tasty_watchlist_resolver was provided."
            )
        symbols = tasty_watchlist_resolver(src.watchlist_name or "", src.max_symbols)
        return [sym.upper() for sym in symbols]

    # Should never hit here with current enums
    return []


# ---------------------------------------------------------------------------
# Width & DTE helpers – these will later be used by trade-ideas
# ---------------------------------------------------------------------------

def choose_width(width_rule: Optional[WidthRule], underlying_price: float) -> Optional[float]:
    """
    Given a WidthRule and an underlying price, choose a spread width.
    This does NOT construct actual strikes – just returns the dollar width.
    """
    if width_rule is None:
        return None

    if width_rule.type == WidthRuleType.INDEX_ALLOWED:
        allowed = width_rule.allowed or []
        if not allowed:
            return None
        # Prefer explicit default; fall back to smallest allowed width.
        if width_rule.default is not None:
            return width_rule.default
        return min(allowed)

    if width_rule.type == WidthRuleType.BY_PRICE_BRACKET:
        if not width_rule.brackets:
            return None
        # Sort brackets by max_price, with None (no upper bound) last.
        sorted_brackets = sorted(
            width_rule.brackets,
            key=lambda b: float("inf") if b.max_price is None else b.max_price,
        )
        for bracket in sorted_brackets:
            if bracket.max_price is None or underlying_price <= bracket.max_price:
                return bracket.width

    return None


def choose_target_dte(
    available_dtes: Sequence[int],
    dte_rule: Optional[DTERule],
) -> Optional[int]:
    """
    Given a list of available DTEs and a DTERule, pick the best candidate.
    You can plug this into your chain-selection logic later.

    Strategy:
    - Filter DTEs to [min, max] if specified.
    - If target given, choose DTE closest to target.
    - Else choose the smallest DTE in range.
    """
    if not available_dtes:
        return None
    if dte_rule is None:
        # Simple fallback: nearest to 45 days or just min?
        target_default = 45
        return min(available_dtes, key=lambda d: abs(d - target_default))

    min_d = dte_rule.min if dte_rule.min is not None else min(available_dtes)
    max_d = dte_rule.max if dte_rule.max is not None else max(available_dtes)

    in_range = [d for d in available_dtes if min_d <= d <= max_d]
    if not in_range:
        return None

    if dte_rule.target is not None:
        return min(in_range, key=lambda d: abs(d - dte_rule.target))

    # No explicit target, just use the smallest DTE in range
    return min(in_range)


# ---------------------------------------------------------------------------
# Strategy × Universe assignment builder
# ---------------------------------------------------------------------------

def build_strategy_universe_assignments(
    cfg: Optional[StrategyConfig] = None,
    tasty_watchlist_resolver: Optional[Callable[[str, Optional[int]], List[str]]] = None,
) -> List[StrategyUniverseAssignment]:
    """
    Expand config into concrete (strategy, universe, symbols) bundles.

    Later, trade-ideas will iterate this to actually pull chains and build TradeIdeas.
    """
    if cfg is None:
        cfg = load_strategy_config()

    assignments: List[StrategyUniverseAssignment] = []

    for strat in cfg.strategies:
        if not strat.enabled:
            continue

        for universe_name in strat.applies_to_universes:
            universe = cfg.universes.get(universe_name)
            if universe is None:
                raise KeyError(
                    f"Strategy '{strat.name}' references unknown universe '{universe_name}'"
                )

            symbols = resolve_universe_tickers(
                universe=universe,
                tasty_watchlist_resolver=tasty_watchlist_resolver,
            )
            if not symbols:
                # Silently skip empty universes – or log if you prefer.
                continue

            assignments.append(
                StrategyUniverseAssignment(
                    strategy=strat,
                    universe=universe,
                    symbols=symbols,
                )
            )

    return assignments

# ---------------------------------------------------------------------------
# Convenience: flatten assignments into a unique symbol list
# ---------------------------------------------------------------------------


def collect_symbols_from_assignments(
    assignments: List[StrategyUniverseAssignment],
) -> List[str]:
    """
    Flatten StrategyUniverseAssignment list into a sorted, de-duplicated
    symbol list for scan-style workflows (trade-ideas).

    This is deliberately dumb: it ignores which strategy/universe a
    symbol came from. Later, if you want per-strategy idea shaping,
    we can keep the structure and pass StrategyTemplate into the
    idea builder.
    """
    unique: set[str] = set()
    for a in assignments:
        for sym in a.symbols:
            unique.add(sym.upper())

    return sorted(unique)


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def debug_print_assignments(
    tasty_watchlist_resolver: Optional[Callable[[str, Optional[int]], List[str]]] = None,
) -> None:
    """
    Debug helper: print Strategy × Universe assignments.

    If no tasty_watchlist_resolver is provided, tasty_watchlist universes
    are resolved to an empty list of symbols (instead of raising).
    """
    if tasty_watchlist_resolver is None:
        # Stub: just return an empty list so we can still inspect
        # static and local_file universes without wiring Tastytrade yet.
        def tasty_watchlist_resolver(name: str, max_symbols: Optional[int]) -> List[str]:
            return []

    cfg = load_strategy_config()
    assignments = build_strategy_universe_assignments(
        cfg=cfg,
        tasty_watchlist_resolver=tasty_watchlist_resolver,
    )

    print("=== Strategy × Universe assignments ===\n")
    if not assignments:
        print("(no assignments – check strategies.yaml)")
        return

    for a in assignments:
        print(
            f"- Strategy: {a.strategy.name} "
            f"(product_type={a.strategy.product_type.value})"
        )
        print(
            f"  Universe: {a.universe.name} "
            f"(source={a.universe.source.type.value})"
        )
        print(f"  Symbols ({len(a.symbols)}): {', '.join(a.symbols[:20])}")
        if len(a.symbols) > 20:
            print(f"  ... (+{len(a.symbols) - 20} more)")
        print()

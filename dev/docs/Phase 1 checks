##step 1##


    ~/Projects/stratdeck-copilot    main ?1                                                               1 ✘    stratdeck-copilot 
❯ python -m stratdeck.cli trade-ideas \
  --universe index_core \
  --strategy short_put_spread_index_45d \
  --json-output
[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}
[
  {
    "symbol": "XSP",
    "data_symbol": "^GSPC",
    "trade_symbol": "XSP",
    "strategy": "short_put_spread",
    "direction": "bullish",
    "vol_context": "normal",
    "rationale": "XSP: slightly_bullish with downside levels providing support \u2013 bullish short put spread structured below key support.",
    "legs": [
      {
        "side": "short",
        "type": "put",
        "strike": 655.3,
        "expiry": "45DTE",
        "quantity": 1
      },
      {
        "side": "long",
        "type": "put",
        "strike": 650.3,
        "expiry": "45DTE",
        "quantity": 1
      }
    ],
    "underlying_price_hint": 660.3,
    "dte_target": 45,
    "spread_width": 5.0,
    "target_delta": 0.2,
    "notes": [
      "Trend regime: choppy_trend, TA bias: 0.19, vol regime: normal.",
      "Volatility context (for options): normal.",
      "[provenance] template=short_put_spread_index_45d universe=index_core"
    ],
    "pop": 0.96,
    "credit_per_width": 0.288,
    "estimated_credit": 1.44
  },
  {
    "symbol": "SPX",
    "data_symbol": "^GSPC",
    "trade_symbol": "SPX",
    "strategy": "short_put_spread",
    "direction": "bullish",
    "vol_context": "normal",
    "rationale": "SPX: slightly_bullish with downside levels providing support \u2013 bullish short put spread structured below key support.",
    "legs": [
      {
        "side": "short",
        "type": "put",
        "strike": 6574.31982421875,
        "expiry": "45DTE",
        "quantity": 1
      },
      {
        "side": "long",
        "type": "put",
        "strike": 6569.31982421875,
        "expiry": "45DTE",
        "quantity": 1
      }
    ],
    "underlying_price_hint": 6607.3,
    "dte_target": 45,
    "spread_width": 5.0,
    "target_delta": 0.2,
    "notes": [
      "Trend regime: choppy_trend, TA bias: 0.19, vol regime: normal.",
      "Volatility context (for options): normal.",
      "[provenance] template=short_put_spread_index_45d universe=index_core"
    ],
    "pop": 0.96,
    "credit_per_width": 0.002,
    "estimated_credit": 0.01
  }
]

##step 2##

❯ cat .stratdeck/last_trade_ideas.json | jq '.[0].underlying_price_hint'
93.59483255896276

##step 3##

❯ rg "underlying_price_hint" stratdeck -n
stratdeck/cli.py
821:        if idea.underlying_price_hint:
822:            click.echo(f"  Underlying hint: {idea.underlying_price_hint:.2f}")

stratdeck/agents/trade_planner.py
50:            "underlying_price_hint spx fallback via xsp failed error=%r", exc
57:            "underlying_price_hint spx fallback via xsp missing price quote=%r",
64:        "underlying_price_hint spx fallback via xsp source=%s xsp_price=%.4f synthetic_price=%.4f",
72:def resolve_underlying_price_hint(
96:                "underlying_price_hint live quote failed symbol=%s error=%r", sym, exc
106:                "underlying_price_hint live quote used symbol=%s source=%s price=%.4f",
115:                "underlying_price_hint live quote missing price symbol=%s quote=%r; falling back",
131:                    "underlying_price_hint chartist fallback failed symbol=%s data_symbol=%s error=%r",
142:                "underlying_price_hint ta fallback non-numeric symbol=%s data_symbol=%s value=%r",
150:        "underlying_price_hint fallback missing live+ta symbol=%s data_symbol=%s provider_present=%s",
197:    underlying_price_hint: Optional[float] = None
360:                    "resolve_underlying_price_hint: provider unavailable mode=%s error=%r",
404:        underlying_hint = resolve_underlying_price_hint(
408:            ta_price_hint=self._infer_underlying_price_hint(
540:            underlying_price_hint=underlying_hint,
756:        underlying_hint = resolve_underlying_price_hint(
760:            ta_price_hint=self._infer_underlying_price_hint(
801:            underlying_price_hint=underlying_hint,
865:    def _infer_underlying_price_hint(

##step 3 ##

❯ rg "get_underlying_price" stratdeck -n


##step 4##

❯ rg "XSP" stratdeck -n
stratdeck/orchestrator.py
539:        idx_symbols = {"SPX", "XSP", "NDX", "RUT"}

stratdeck/config/strategies.yaml
10:    description: "Core index underlyings for SPX/XSP style strategies"
14:      tickers: [SPX, XSP]
67:      allowed: [1,5, 10, 25]   # SPX/XSP spread widths to consider

stratdeck/cli.py
495:    help="One or more symbols to analyse (e.g. -s SPX -s XSP).",
535:      python -m stratdeck.cli chartist -s SPX -s XSP -H short_premium_range

stratdeck/data/factory.py
37:    service = LiveMarketDataService(session=session, symbols=["SPX", "XSP"])

stratdeck/agents/trader.py
100:                or symbol.upper() in {"SPX", "XSP", "RUT", "NDX"}
300:        if symbol in {"SPX", "XSP"}:

stratdeck/data/tasty_provider.py
31:    INDEX_SYMBOLS = {"SPX", "RUT", "NDX", "VIX", "XSP"}

stratdeck/conf/stratdeck.yml
24:  XSP: 1
30:  - XSP

stratdeck/agents/trade_planner.py
42:    If SPX quotes fail, try XSP and rescale (approx 1/10th the size).
47:        quote = fetcher("XSP")

stratdeck/agents/scout.py
78:        liquid_syms = {"SPX", "XSP", "QQQ", "IWM", "SPY"}

stratdeck/tools/vol.py
22:        return {"SPX": 0.35, "XSP": 0.38, "QQQ": 0.29, "IWM": 0.33}

stratdeck/tools/ta.py
21:SPX_XSP_DATA_MAP = {
27:    "XSP": {
29:        "trade_symbol": "XSP",
40:    if s in SPX_XSP_DATA_MAP:
41:        m = SPX_XSP_DATA_MAP[s]
631:            "XSP": "^GSPC",   # mini SPX – structurally same index; scale is fine for TA
716:        # 🔹 NEW: map SPX/XSP → ^GSPC (or other aliases) for yfinance

##step 5##

❯ rg "strike" stratdeck/tools -n
stratdeck/tools/greeks.py
37:def _nearest_option(options: Iterable[Dict[str, Any]], strike: float) -> Optional[Dict[str, Any]]:
42:            diff = abs(float(opt.get("strike", 0.0)) - strike)
67:        strike = _coerce(_leg_attr(leg, "strike", 0.0))
70:        quote = _nearest_option(options, strike)

stratdeck/tools/chains.py
31:    strikes = [round(px * (0.9 + i * 0.01), 2) for i in range(n)]
35:    for k, strike in enumerate(strikes):
44:                "strike": strike,
55:                "strike": strike,
83:    long_strike = round(float(short.get("strike", 0)) - float(width), 2)
84:    long = min(puts, key=lambda p: abs(float(p.get("strike", 0)) - long_strike))
90:        "width": round(abs(float(short.get("strike", 0)) - float(long.get("strike", 0))), 2),

stratdeck/tools/chain_pricing_adapter.py
30:      - strike (float)
101:        - Looks up chain quotes for the leg strikes and derives:
140:            short_strike = float(short_leg.strike)
141:            long_strike = float(long_leg.strike)
145:        width = abs(short_strike - long_strike)
172:        def _nearest_quote(strike: float) -> Optional[Dict[str, Any]]:
177:                    s = float(row.get("strike"))
180:                diff = abs(s - strike)
205:        short_q = _nearest_quote(short_strike)
206:        long_q = _nearest_quote(long_strike)
276:                    "strike": float(short_strike),
282:                    "strike": float(long_strike),

stratdeck/tools/chartist.py
45:            "  especially short premium, long premium, and strike selection.\n"
62:            "- Be concrete about where strikes might be placed relative to support/resistance or ranges.\n"

stratdeck/tools/orders.py
29:    strike: float
111:    strike = data.get("strike")
113:        strike = float(strike)
115:        strike = strike
126:        "strike": strike,
217:                strike=data.get("strike"),
234:        "strike": float(leg.strike),
273:                    "strike": float(leg.get("strike")),
284:            strike=float(legs_in["strike"]),

##step 6##

❯ ls tests | grep -i xsp
test_xsp_strike_scaling.py

##step 7##

❯ pytest tests/test_xsp_strike_scaling.py
============================================================ test session starts ============================================================
platform darwin -- Python 3.11.14, pytest-9.0.1, pluggy-1.6.0
rootdir: /Users/christaylor/Projects/stratdeck-copilot
plugins: anyio-4.11.0
collected 2 items

tests/test_xsp_strike_scaling.py ..                                                                                                   [100%]

============================================================= 2 passed in 0.65s =============================================================

##step 8##

❯ rg "get_chains" stratdeck -n
❯ rg "tasty" stratdeck/tools -n
❯ rg "429 Too Many Requests" -n
dev/codex/live-quote-streaming.md
38:- This causes `HTTP 429 Too Many Requests` from Tastytrade.

##step 9##

❯ for i in {1..5}; do
  python -m stratdeck.cli trade-ideas \
    --universe index_core \
    --strategy short_put_spread_index_45d \
    --json-output > /dev/null || echo "FAILED $i"
done

[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}
[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}
[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}
[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}
[trade-ideas] Running scan for 2 symbols: SPX, XSP
[trade-ideas] candidate before filters: {'symbol': 'XSP', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.32, 'credit_per_width': 0.288, 'estimated_credit': 1.44}
[trade-ideas] candidate before filters: {'symbol': 'SPX', 'strategy_type': 'short_put_spread', 'direction': 'bullish', 'spread_width': 5.0, 'dte_target': 45, 'pop': 0.96, 'ivr': 0.3, 'credit_per_width': 0.002, 'estimated_credit': 0.01}

##step 10##

❯ rg "TradeIdea" stratdeck -n
stratdeck/cli.py
712:    help="Emit raw TradeIdea structures as JSON instead of formatted text.",
931:    help="Index into last TradeIdeas set (0-based)",
956:    Enter a trade directly from the last TradeIdeas run.
965:            "No TradeIdeas found. Run 'trade-ideas --json-output' first."
994:    help="Index into last TradeIdeas set (0-based)",
1006:    Preview compliance outcome for a saved TradeIdea without placing anything.
1011:            "No TradeIdeas found. Run 'trade-ideas --json-output' first."
1043:    Run all TradeIdeas from the last trade-ideas run through ComplianceAgent.
1052:            "No TradeIdeas found. Run 'trade-ideas --json-output' first."

stratdeck/strategies.py
172:    Optional filters that a candidate TradeIdea must satisfy.

stratdeck/strategy_engine.py
222:    Later, trade-ideas will iterate this to actually pull chains and build TradeIdeas.

stratdeck/agents/trader.py
200:        Dry-run a TradeIdea through the full build_order_plan + ComplianceAgent
237:        - adapt TradeIdea -> spread_plan
285:            raise ValueError("TradeIdea has no symbol/trade_symbol/underlying set")
333:    # --- POP / credit_per_width ranking helpers for TradeIdeas ---
335:        # --- POP / credit_per_width ranking helpers for TradeIdeas ---
338:        """Best-effort extraction of a numeric metric from a TradeIdea or dict."""

stratdeck/agents/trade_planner.py
181:class TradeIdea:
229:      - list[TradeIdea]
259:    ) -> List[TradeIdea]:
261:        Main entry: turn TA-enriched scan rows into a list of TradeIdea objects.
263:        ideas: List[TradeIdea] = []
294:    ) -> List[TradeIdea]:
301:        rules, filters) to shape each TradeIdea.
303:        The external TradeIdea schema remains unchanged.
313:        ideas: List[TradeIdea] = []
326:            per_symbol_ideas: List[TradeIdea] = []
373:    ) -> Optional[TradeIdea]:
378:        a TradeIdea, while keeping the TradeIdea schema unchanged.
495:                        # into TradeIdea.target_delta. This can be wired to
531:        idea = TradeIdea(
729:    ) -> List[TradeIdea]:
792:        idea = TradeIdea(

stratdeck/tools/ideas.py
12:    Load the last TradeIdeas JSON produced by:

stratdeck/tools/scan_cache.py
13:    - ideas: TradeIdea objects (or dicts) emitted by TradePlanner.
34:    Save the most recent TradeIdea list for follow-up commands.

stratdeck/tools/orders.py
15:    from stratdeck.agents.trade_planner import TradeIdea, TradeLeg
366:    trade_idea: "TradeIdea",
374:    Paper-only entry point for TradeIdeas.

##step 11#

❯ rg "trade_ideas" stratdeck -n
stratdeck/cli.py
32:    store_trade_ideas,
37:LAST_TRADE_IDEAS_PATH = Path(".stratdeck/last_trade_ideas.json")
94:def _build_trade_ideas_for_symbols(
131:def _build_trade_ideas_for_tasks(
719:def trade_ideas(
771:    ideas = _build_trade_ideas_for_tasks(
780:    store_trade_ideas(ideas)
855:    # 1. Load last_trade_ideas.json
959:      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json
1046:      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json
1144:    default=Path(".stratdeck/last_trade_ideas.json"),

stratdeck/orchestrator.py
36:        default_factory=lambda: Path(".stratdeck/last_trade_ideas.json")

stratdeck/tools/ideas.py
7:DEFAULT_IDEAS_PATH = Path(".stratdeck/last_trade_ideas.json")
13:      python -m stratdeck.cli trade-ideas --json-output .stratdeck/last_trade_ideas.json

stratdeck/agents/trader.py
422:    def rank_trade_ideas(self, ideas: List[Any]) -> List[tuple[Any, float]]:
467:        ranked = self.rank_trade_ideas(ideas)

stratdeck/tools/scan_cache.py
32:def store_trade_ideas(ideas: Iterable[Any]) -> None:

##step 12##

❯ rg "vol_context" stratdeck -n
stratdeck/cli.py
802:            f"vol {idea.vol_context} | target {idea.dte_target or dte_target} DTE"

stratdeck/agents/trade_planner.py
194:    vol_context: str  # "normal", "elevated", "expansion_likely"
537:            vol_context=vol_bias,
798:            vol_context=vol_bias,

##step 13##

❯ sed -n '1,200p' stratdeck/strategy_engine.py

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


@dataclass
class SymbolStrategyTask:
    """
    A single (symbol, strategy, universe) task for the trade-ideas engine.
    This is what the idea engine will ultimately iterate over.
    """

    symbol: str
    strategy: StrategyTemplate
    universe: UniverseConfig



def build_symbol_strategy_tasks(
    assignments: Sequence[StrategyUniverseAssignment],
) -> List[SymbolStrategyTask]:
    tasks: List[SymbolStrategyTask] = []
    for a in assignments:
        for sym in a.symbols:
            tasks.append(
                SymbolStrategyTask(
                    symbol=sym,
                    strategy=a.strategy,
                    universe=a.universe,
                )
            )
    return tasks


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

    ##step 14##

    ❯ rg "StrategyFilters" stratdeck -n
sed -n '1,200p' stratdeck/strategies.py  # adjust path if needed

stratdeck/strategies.py
170:class StrategyFilters(BaseModel):
201:    filters: Optional[StrategyFilters] = None
# stratdeck/strategies.py

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Union, Literal

import yaml
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class UniverseSourceType(str, Enum):
    STATIC = "static"
    LOCAL_FILE = "local_file"
    TASTY_WATCHLIST = "tasty_watchlist"


class ProductType(str, Enum):
    ANY = "any"
    INDEX = "index"
    EQUITY = "equity"
    ETF = "etf"


class WidthRuleType(str, Enum):
    INDEX_ALLOWED = "index_allowed"
    BY_PRICE_BRACKET = "by_price_bracket"


# ---------------------------------------------------------------------------
# UNIVERSE CONFIG
# ---------------------------------------------------------------------------

class UniverseSource(BaseModel):
    """
    Describes where the tickers for a universe come from.

    type:
      - static:          tickers provided directly
      - local_file:      tickers loaded from a local file
      - tasty_watchlist: tickers pulled from a Tastytrade watchlist
    """

    type: UniverseSourceType
    tickers: Optional[List[str]] = None       # for static
    path: Optional[str] = None                # for local_file
    watchlist_name: Optional[str] = None      # for tasty_watchlist
    max_symbols: Optional[int] = None         # optional cap on symbols

    @model_validator(mode="after")
    def validate_by_type(self) -> "UniverseSource":
        if self.type == UniverseSourceType.STATIC:
            if not self.tickers:
                raise ValueError("static universe requires 'tickers'")
        elif self.type == UniverseSourceType.LOCAL_FILE:
            if not self.path:
                raise ValueError("local_file universe requires 'path'")
        elif self.type == UniverseSourceType.TASTY_WATCHLIST:
            if not self.watchlist_name:
                raise ValueError("tasty_watchlist universe requires 'watchlist_name'")
        return self


class UniverseConfig(BaseModel):
    """
    A named universe of underlyings: e.g. 'index_core', 'equity_core'.
    """

    name: str
    description: Optional[str] = None
    product_type: ProductType = ProductType.ANY
    source: UniverseSource


# ---------------------------------------------------------------------------
# STRATEGY CONFIG – DTE, DELTA, WIDTH, FILTERS
# ---------------------------------------------------------------------------

class DTERule(BaseModel):
    """
    Target days-to-expiry band for a strategy.
    """

    target: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None

    @model_validator(mode="after")
    def validate_range(self) -> "DTERule":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("dte.min cannot be greater than dte.max")
        return self


class DeltaBand(BaseModel):
    """
    Describes a band/target for the short leg delta.
    All values are absolute deltas (0.0–1.0).
    """

    target: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None

    @model_validator(mode="after")
    def validate_range(self) -> "DeltaBand":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("delta.min cannot be greater than delta.max")
        return self


class DeltaRule(BaseModel):
    """
    For now we focus on the short legs; long legs are derived from width.
    """

    short_leg: DeltaBand


class PriceBracket(BaseModel):
    """
    Single price bracket for by_price_bracket width rules.
    """

    max_price: Optional[float] = Field(
        default=None,
        description="Upper bound for this price bracket (None = no upper bound).",
    )
    width: float


class WidthRule(BaseModel):
    """
    Rules for choosing spread width given product type and underlying price.

    type:
      - index_allowed:     choose from a fixed set of allowed widths (e.g. [5, 10, 25])
      - by_price_bracket:  choose width based on underlying price brackets
    """

    type: WidthRuleType
    # For index_allowed
    allowed: Optional[List[float]] = None
    default: Optional[float] = None

    # For by_price_bracket
    brackets: Optional[List[PriceBracket]] = None

    @model_validator(mode="after")
    def validate_by_type(self) -> "WidthRule":
        if self.type == WidthRuleType.INDEX_ALLOWED:
            if not self.allowed:
                raise ValueError("index_allowed width_rule requires 'allowed'")
        elif self.type == WidthRuleType.BY_PRICE_BRACKET:
            if not self.brackets:
                raise ValueError("by_price_bracket width_rule requires 'brackets'")
        return self


class StrategyFilters(BaseModel):
    """
    Optional filters that a candidate TradeIdea must satisfy.
    All are expressed as fractions, not percents (0.50 = 50%).
    """

    min_pop: Optional[float] = None
    max_pop: Optional[float] = None
    min_credit_per_width: Optional[float] = None
    min_ivr: Optional[float] = None
    max_ivr: Optional[float] = None


class StrategyTemplate(BaseModel):
    """
    Declarative strategy template used by the trade-ideas engine and orchestrator.
    """

    name: str
    label: Optional[str] = None
    enabled: bool = True

    applies_to_universes: List[str]

    product_type: ProductType = ProductType.ANY
    order_side: Literal["buy", "sell"] = "sell"
    option_type: Literal["call", "put", "both"] = "put"

    dte: Optional[DTERule] = None
    delta: Optional[DeltaRule] = None
    width_rule: Optional[WidthRule] = None

    ##step 15##

    ❯ rg "min_ivr" stratdeck -n
rg "filters" stratdeck -n
rg "passes_filters" stratdeck -n

stratdeck/strategies.py
179:    min_ivr: Optional[float] = None
321:                f"min_ivr={s.filters.min_ivr}, "

stratdeck/config/strategies.yaml
73:      min_ivr: 0.20
109:      min_ivr: 0.20
141:      min_ivr: 0.30

stratdeck/agents/trade_planner.py
616:        min_ivr = getattr(filters, "min_ivr", None)
641:        if min_ivr is not None and ivr is not None and ivr < min_ivr:
644:                    "Filter reject: symbol=%s strategy=%s reason=ivr min_ivr=%.2f ivr=%.2f",
647:                    float(min_ivr),
stratdeck/orchestrator.py
25:    integration is wired in. POP / credit-per-width filters are only applied
105:        - Apply POP / credit-per-width / index-vs-equity filters.
135:            # Apply filters and compute scores
140:                if not self._passes_filters(c):
146:                reason = "no_candidates_passed_filters"
147:                self.logger.info("No candidates passed filters.")
384:    def _passes_filters(self, candidate: VettedCandidate) -> bool:
386:        Apply config-based filters (POP, credit/width, index vs equity).

stratdeck/cli.py
699:    "universe_filters",
705:    "strategy_filters",
723:    universe_filters: tuple[str, ...],
724:    strategy_filters: tuple[str, ...],
738:    if universe_filters:
739:        universe_filter_set = {u.strip() for u in universe_filters}
742:    if strategy_filters:
743:        strategy_filter_set = {s.strip() for s in strategy_filters}
748:            "No matching strategy/universe assignments. Check filters/config.",
882:        # This happens if all ideas fail POP/credit filters
1174:    - Vets and filters candidates using TraderAgent + Compliance.

stratdeck/strategies.py
172:    Optional filters that a candidate TradeIdea must satisfy.
201:    filters: Optional[StrategyFilters] = None
316:        if s.filters:
319:                f"min_pop={s.filters.min_pop}, "
320:                f"min_credit_per_width={s.filters.min_credit_per_width}, "
321:                f"min_ivr={s.filters.min_ivr}, "
322:                f"max_ivr={s.filters.max_ivr}"

stratdeck/agents/trader.py
361:        Returns None if the idea fails hard filters.
466:        """Select the top-ranked idea or raise if none survive filters."""
469:            raise ValueError("No trade ideas passed POP filters.")

stratdeck/agents/trade_planner.py
301:        rules, filters) to shape each TradeIdea.
377:        Uses TA context + StrategyTemplate (dte, width_rule, filters) to produce
526:            print("[trade-ideas] candidate before filters:", candidate, file=sys.stderr)
528:        if not self._passes_strategy_filters(candidate, task.strategy):
606:    def _passes_strategy_filters(
611:        filters = getattr(strategy, "filters", None)
612:        if filters is None:
615:        min_pop = getattr(filters, "min_pop", None)
616:        min_ivr = getattr(filters, "min_ivr", None)
617:        min_credit_per_width = getattr(filters, "min_credit_per_width", None)

stratdeck/config/strategies.yaml
43:# - filters: POP, credit/width, IVR, etc.
70:    filters:
106:    filters:
138:    filters:
stratdeck/orchestrator.py
140:                if not self._passes_filters(c):
384:    def _passes_filters(self, candidate: VettedCandidate) -> bool:

##step 16##


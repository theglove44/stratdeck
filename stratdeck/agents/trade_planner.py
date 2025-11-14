# stratdeck/agents/trade_planner.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class TradeLeg:
    """
    A single options leg.

    side: "short" or "long"
    type: "call" or "put"
    strike: numeric strike price
    expiry: string or date repr (e.g. "2025-01-17" or "45DTE" placeholder)
    quantity: contracts per leg (positive integer; side encodes long/short)
    """
    side: str
    type: str
    strike: float
    expiry: Optional[str]
    quantity: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeIdea:
    """
    A structured trade idea generated from TA + scout context.

    This is intentionally broker-agnostic. Your existing trader.py module
    can map this into real option contracts and order plans.
    """
    symbol: str
    strategy: str                   # "iron_condor", "short_put_spread", etc.
    direction: str                  # "bullish", "bearish", "neutral", etc.
    vol_context: str                # "normal", "elevated", "expansion_likely"
    rationale: str                  # one-paragraph explanation
    legs: List[TradeLeg]
    underlying_price_hint: Optional[float] = None
    dte_target: Optional[int] = None
    notes: List[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["legs"] = [leg.to_dict() for leg in self.legs]
        return d


class TradePlanner:
    """
    TradePlanner: consumes TA-enriched scan rows and proposes options structures.

    Inputs (per scan row):
      {
        "symbol": "SPX",
        "score": 0.48,              # from ScoutAgent (optional)
        "ta": { ... TA_RESULT ...}, # from ChartistEngine
        "ta_score": 0.12,
        "ta_directional_bias": "slightly_bullish",
        "ta_vol_bias": "normal",
        "strategy_hint": "short_premium_range" | "short_premium_trend" | "long_premium_breakout" (optional)
      }

    Outputs:
      - list[TradeIdea]
    """

    def __init__(
        self,
        chains_client: Any = None,
        pricing_client: Any = None,
    ) -> None:
        """
        chains_client / pricing_client are reserved for future integration
        with stratdeck.tools.chains / stratdeck.tools.pricing.

        Right now TradePlanner works purely off TA structure and does not yet
        look at real option chains.
        """
        self.chains_client = chains_client
        self.pricing_client = pricing_client

    # ---------- Public API ----------

    def generate_from_scan_results(
        self,
        scan_rows: Sequence[Dict[str, Any]],
        default_strategy: str = "short_premium_range",
        dte_target: int = 45,
        max_per_symbol: int = 1,
    ) -> List[TradeIdea]:
        """
        Main entry: turn TA-enriched scan rows into a list of TradeIdea objects.
        """
        ideas: List[TradeIdea] = []

        for row in scan_rows:
            symbol = row.get("symbol")
            if not symbol:
                continue

            ta = row.get("ta") or {}
            strategy_hint = row.get("strategy_hint", default_strategy)

            per_symbol_ideas = self._generate_for_symbol(
                symbol=symbol,
                row=row,
                ta=ta,
                strategy_hint=strategy_hint,
                dte_target=dte_target,
            )

            if not per_symbol_ideas:
                continue

            ideas.extend(per_symbol_ideas[:max_per_symbol])

        return ideas

    # ---------- Internals ----------

    def _generate_for_symbol(
        self,
        symbol: str,
        row: Dict[str, Any],
        ta: Dict[str, Any],
        strategy_hint: str,
        dte_target: int,
    ) -> List[TradeIdea]:
        scores = ta.get("scores", {}) or {}
        structure = ta.get("structure", {}) or {}
        trend_regime = (ta.get("trend_regime") or {}).get("state", "unknown")
        vol_regime = (ta.get("vol_regime") or {}).get("state", "unknown")

        dir_bias = row.get("ta_directional_bias", scores.get("directional_bias", "neutral"))
        vol_bias = row.get("ta_vol_bias", scores.get("vol_bias", "normal"))
        ta_score = scores.get("ta_bias", 0.0)

        support_levels: List[float] = structure.get("support") or []
        resistance_levels: List[float] = structure.get("resistance") or []
        range_info: Optional[Dict[str, Any]] = structure.get("range") or None

        strategy_type = self._pick_strategy_type(
            strategy_hint=strategy_hint,
            dir_bias=dir_bias,
            vol_bias=vol_bias,
            trend_regime=trend_regime,
        )
        if strategy_type == "skip":
            return []

        underlying_hint = self._infer_underlying_price_hint(
            support_levels,
            resistance_levels,
            range_info,
        )

        legs = self._build_legs_from_ta(
            strategy_type=strategy_type,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            underlying_hint=underlying_hint,
            dte_target=dte_target,
        )

        if not legs:
            return []

        direction = self._direction_from_strategy(strategy_type, dir_bias)
        rationale, notes = self._build_rationale(
            symbol=symbol,
            strategy_type=strategy_type,
            dir_bias=dir_bias,
            vol_bias=vol_bias,
            trend_regime=trend_regime,
            ta_score=ta_score,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            vol_regime=vol_regime,
        )

        idea = TradeIdea(
            symbol=symbol,
            strategy=strategy_type,
            direction=direction,
            vol_context=vol_bias,
            rationale=rationale,
            legs=legs,
            underlying_price_hint=underlying_hint,
            dte_target=dte_target,
            notes=notes,
        )
        return [idea]

    def _pick_strategy_type(
        self,
        strategy_hint: str,
        dir_bias: str,
        vol_bias: str,
        trend_regime: str,
    ) -> str:
        """
        Decide a coarse strategy type using the indicated style + TA context.

        Returns one of:
          - "iron_condor"
          - "short_put_spread"
          - "short_call_spread"
          - "long_call_spread"
          - "long_put_spread"
          - "skip"
        """
        strategy_hint = (strategy_hint or "").lower()

        if "bullish" in dir_bias:
            directional = "bullish"
        elif "bearish" in dir_bias:
            directional = "bearish"
        else:
            directional = "neutral"

        # 1) Range short premium
        if strategy_hint == "short_premium_range":
            if directional == "neutral":
                return "iron_condor"
            if directional == "bullish":
                return "short_put_spread"
            if directional == "bearish":
                return "short_call_spread"

        # 2) Trend short premium
        if strategy_hint == "short_premium_trend":
            if directional == "bullish":
                return "short_put_spread"
            if directional == "bearish":
                return "short_call_spread"
            if trend_regime in ("uptrend", "downtrend"):
                return "skip"

        # 3) Long premium breakout
        if strategy_hint == "long_premium_breakout":
            if directional == "bullish":
                return "long_call_spread"
            if directional == "bearish":
                return "long_put_spread"
            if vol_bias == "expansion_likely":
                return "skip"

        return "skip"

    def _infer_underlying_price_hint(
        self,
        support_levels: List[float],
        resistance_levels: List[float],
        range_info: Optional[Dict[str, Any]],
    ) -> Optional[float]:
        """
        Approximate current price using TA structure only.
        Later this can be replaced with actual last price from chains/quotes.
        """
        if range_info and "low" in range_info and "high" in range_info:
            low = range_info["low"]
            high = range_info["high"]
            if high > low:
                return (low + high) / 2.0

        if support_levels and resistance_levels:
            return (support_levels[-1] + resistance_levels[0]) / 2.0

        if resistance_levels:
            return resistance_levels[-1]
        if support_levels:
            return support_levels[-1]

        return None

    def _build_legs_from_ta(
        self,
        strategy_type: str,
        support_levels: List[float],
        resistance_levels: List[float],
        underlying_hint: Optional[float],
        dte_target: int,
    ) -> List[TradeLeg]:
        """
        Build a logical vertical/IC from TA levels.

        This does not yet resolve to actual strikes in a real option chain.
        """
        legs: List[TradeLeg] = []

        def nearest_below(levels: List[float], ref: float) -> Optional[float]:
            below = [lvl for lvl in levels if lvl < ref]
            return below[-1] if below else (levels[-1] if levels else None)

        def nearest_above(levels: List[float], ref: float) -> Optional[float]:
            above = [lvl for lvl in levels if lvl > ref]
            return above[0] if above else (levels[0] if levels else None)

        expiry_str = f"{dte_target}DTE"  # placeholder label

        if underlying_hint is None:
            # crude fallback
            if support_levels:
                underlying_hint = support_levels[-1]
            elif resistance_levels:
                underlying_hint = resistance_levels[0]
            else:
                return []

        ref_price = float(underlying_hint)

        # Basic width: ~1% of ref price
        spread_width = max(ref_price * 0.01, 0.5)

        if strategy_type == "iron_condor":
            short_put_strike = nearest_below(support_levels, ref_price) or (ref_price - spread_width)
            short_call_strike = nearest_above(resistance_levels, ref_price) or (ref_price + spread_width)

            long_put_strike = short_put_strike - spread_width
            long_call_strike = short_call_strike + spread_width

            legs = [
                TradeLeg(side="short", type="put", strike=float(short_put_strike), expiry=expiry_str),
                TradeLeg(side="long", type="put", strike=float(long_put_strike), expiry=expiry_str),
                TradeLeg(side="short", type="call", strike=float(short_call_strike), expiry=expiry_str),
                TradeLeg(side="long", type="call", strike=float(long_call_strike), expiry=expiry_str),
            ]

        elif strategy_type == "short_put_spread":
            short_strike = nearest_below(support_levels, ref_price) or (ref_price - spread_width)
            long_strike = short_strike - spread_width
            legs = [
                TradeLeg(side="short", type="put", strike=float(short_strike), expiry=expiry_str),
                TradeLeg(side="long", type="put", strike=float(long_strike), expiry=expiry_str),
            ]

        elif strategy_type == "short_call_spread":
            short_strike = nearest_above(resistance_levels, ref_price) or (ref_price + spread_width)
            long_strike = short_strike + spread_width
            legs = [
                TradeLeg(side="short", type="call", strike=float(short_strike), expiry=expiry_str),
                TradeLeg(side="long", type="call", strike=float(long_strike), expiry=expiry_str),
            ]

        elif strategy_type == "long_call_spread":
            long_strike = ref_price
            short_strike = long_strike + spread_width
            legs = [
                TradeLeg(side="long", type="call", strike=float(long_strike), expiry=expiry_str),
                TradeLeg(side="short", type="call", strike=float(short_strike), expiry=expiry_str),
            ]

        elif strategy_type == "long_put_spread":
            long_strike = ref_price
            short_strike = long_strike - spread_width
            legs = [
                TradeLeg(side="long", type="put", strike=float(long_strike), expiry=expiry_str),
                TradeLeg(side="short", type="put", strike=float(short_strike), expiry=expiry_str),
            ]

        return legs

    def _direction_from_strategy(self, strategy_type: str, dir_bias: str) -> str:
        if strategy_type in ("short_put_spread", "long_call_spread"):
            return "bullish"
        if strategy_type in ("short_call_spread", "long_put_spread"):
            return "bearish"
        if strategy_type == "iron_condor":
            if "bullish" in dir_bias:
                return "slightly_bullish"
            if "bearish" in dir_bias:
                return "slightly_bearish"
            return "neutral"
        return "neutral"

    def _build_rationale(
        self,
        symbol: str,
        strategy_type: str,
        dir_bias: str,
        vol_bias: str,
        trend_regime: str,
        ta_score: float,
        support_levels: List[float],
        resistance_levels: List[float],
        vol_regime: str,
    ) -> Tuple[str, List[str]]:
        notes: List[str] = []

        if strategy_type == "iron_condor":
            rationale = (
                f"{symbol}: range / non-trending conditions with {vol_bias} volatility – "
                "structure favours short premium in both directions."
            )
            if support_levels and resistance_levels:
                notes.append(
                    f"Using support near {support_levels[-1]:.2f} and resistance near {resistance_levels[0]:.2f} "
                    "as anchors for IC wings."
                )
        elif strategy_type == "short_put_spread":
            rationale = (
                f"{symbol}: {dir_bias} with downside levels providing support – "
                "bullish short put spread structured below key support."
            )
        elif strategy_type == "short_call_spread":
            rationale = (
                f"{symbol}: {dir_bias} with upside resistance zones – "
                "bearish short call spread structured above key resistance."
            )
        elif strategy_type == "long_call_spread":
            rationale = (
                f"{symbol}: {dir_bias} with potential breakout and {vol_bias} volatility – "
                "bullish debit spread to participate in upside."
            )
        elif strategy_type == "long_put_spread":
            rationale = (
                f"{symbol}: {dir_bias} with downside continuation risk – "
                "bearish debit spread to participate in further downside."
            )
        else:
            rationale = f"{symbol}: no clear structured trade idea."

        notes.append(f"Trend regime: {trend_regime}, TA bias: {ta_score:.2f}, vol regime: {vol_regime}.")
        notes.append(f"Volatility context (for options): {vol_bias}.")
        return rationale, notes
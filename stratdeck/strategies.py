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
    FIXED = "fixed"


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
      - fixed:             enforce a single fixed width
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
        elif self.type == WidthRuleType.FIXED:
            if self.default is None and not self.allowed:
                raise ValueError("fixed width_rule requires 'default' or 'allowed'")
            if self.allowed is None:
                self.allowed = [self.default] if self.default is not None else None
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


class ExpiryRules(BaseModel):
    """
    Additional expiry constraints beyond the DTE band.
    """

    monthlies_only: bool = False
    earnings_buffer_days: Optional[int] = None


class RiskLimits(BaseModel):
    """
    Risk guardrails that should be enforced per candidate.
    """

    max_buying_power: Optional[float] = None
    max_positions_per_symbol: Optional[int] = None
    max_position_delta: Optional[float] = None

    @model_validator(mode="after")
    def validate_limits(self) -> "RiskLimits":
        if self.max_buying_power is not None and self.max_buying_power < 0:
            raise ValueError("max_buying_power must be non-negative")
        if self.max_positions_per_symbol is not None and self.max_positions_per_symbol < 0:
            raise ValueError("max_positions_per_symbol must be non-negative")
        if self.max_position_delta is not None and self.max_position_delta < 0:
            raise ValueError("max_position_delta must be non-negative")
        return self


class ExitRules(BaseModel):
    """
    Optional per-strategy exit hints for position monitoring.
    """

    profit_target_fraction: Optional[float] = None
    dte_exit_target: Optional[int] = None
    dte_exit_flex: Optional[int] = None
    respect_earnings: bool = True


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
    expiry_rules: Optional[ExpiryRules] = None
    delta: Optional[DeltaRule] = None
    width_rule: Optional[WidthRule] = None
    filters: Optional[StrategyFilters] = None
    risk_limits: Optional[RiskLimits] = None
    exit_rules: Optional[ExitRules] = None
    allowed_trend_regimes: Optional[List[str]] = None
    allowed_vol_regimes: Optional[List[str]] = None
    blocked_trend_regimes: Optional[List[str]] = None
    blocked_vol_regimes: Optional[List[str]] = None

    @field_validator("applies_to_universes")
    @classmethod
    def non_empty_universes(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("strategy.applies_to_universes must not be empty")
        return v


# ---------------------------------------------------------------------------
# ROOT CONFIG
# ---------------------------------------------------------------------------

class StrategyConfig(BaseModel):
    """
    Top-level configuration object loaded from strategies.yaml.
    """

    universes: Dict[str, UniverseConfig]
    strategies: List[StrategyTemplate]

    @field_validator("universes", mode="before")
    @classmethod
    def attach_universe_names(cls, v):
        """
        The YAML uses:
            universes:
              index_core:
                product_type: index
                source: ...
        Here we convert that dict into UniverseConfig objects
        while injecting the key as the 'name' field.
        """
        if not isinstance(v, dict):
            return v

        new_v: Dict[str, object] = {}

        for name, cfg in v.items():
            # Case 1: already a UniverseConfig instance (e.g. tests constructing
            # StrategyConfig(universes=...)).
            if isinstance(cfg, UniverseConfig):
                # Ensure the embedded name matches the dict key.
                if cfg.name != name:
                    cfg = cfg.model_copy(update={"name": name})
                new_v[name] = cfg
                continue

            # Case 2: raw dict from YAML.
            if isinstance(cfg, dict):
                data = dict(cfg)
                data.setdefault("name", name)
                new_v[name] = data
                continue

            # Fallback: leave as-is (let Pydantic complain if it can't coerce).
            new_v[name] = cfg

        return new_v


# ---------------------------------------------------------------------------
# LOADER
# ---------------------------------------------------------------------------

# Default path: stratdeck/config/strategies.yaml
CONFIG_DIR = Path(__file__).resolve().parent / "config"
DEFAULT_STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategies.yaml"


def load_strategy_config(
    path: Optional[Union[str, Path]] = None,
) -> StrategyConfig:
    """
    Load and validate the strategy/universe configuration from YAML.

    Args:
        path: Optional override path to a YAML file. If not provided,
              uses stratdeck/config/strategies.yaml relative to this file.

    Returns:
        StrategyConfig instance with universes + strategy templates.
    """
    if path is None:
        path = DEFAULT_STRATEGY_CONFIG_PATH

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Strategy config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return StrategyConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# DEBUG / MANUAL TEST ENTRYPOINT
# ---------------------------------------------------------------------------

def _debug_print_config(cfg: StrategyConfig) -> None:
    print("=== StrategyConfig loaded ===\n")

    print("Universes:")
    for name, u in cfg.universes.items():
        print(
            f"- {name}: product_type={u.product_type.value}, "
            f"source={u.source.type.value}"
        )
        if u.source.type.value == "static":
            print(f"    tickers={u.source.tickers}")
        elif u.source.type.value == "local_file":
            print(f"    path={u.source.path}")
        elif u.source.type.value == "tasty_watchlist":
            print(
                f"    watchlist_name={u.source.watchlist_name}, "
                f"max_symbols={u.source.max_symbols}"
            )

    print("\nStrategies:")
    for s in cfg.strategies:
        print(
            f"- {s.name} "
            f"(enabled={s.enabled}, "
            f"product_type={s.product_type.value}, "
            f"universes={s.applies_to_universes})"
        )
        if s.dte:
            print(f"    DTE: target={s.dte.target}, min={s.dte.min}, max={s.dte.max}")
        if s.delta and s.delta.short_leg:
            sl = s.delta.short_leg
            print(
                f"    Short-leg delta: target={sl.target}, min={sl.min}, max={sl.max}"
            )
        if s.width_rule:
            print(f"    Width rule type={s.width_rule.type.value}")
        if s.filters:
            print(
                "    Filters: "
                f"min_pop={s.filters.min_pop}, "
                f"min_credit_per_width={s.filters.min_credit_per_width}, "
                f"min_ivr={s.filters.min_ivr}, "
                f"max_ivr={s.filters.max_ivr}"
            )


if __name__ == "__main__":
    cfg = load_strategy_config()
    _debug_print_config(cfg)

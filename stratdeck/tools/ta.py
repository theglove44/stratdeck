# stratdeck/tools/ta.py

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf  # optional, used for mock/live data if no client passed
except Exception:  # pragma: no cover - optional dependency
    yf = None


Timeframe = str


@dataclass
class Regime:
    state: str
    confidence: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class MomentumState:
    state: str
    rsi: float
    rsi_slope: float
    macd_hist: float
    macd_hist_slope: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RangeInfo:
    low: float
    high: float
    in_range: bool
    position_in_range: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class StructureInfo:
    support: List[float]
    resistance: List[float]
    range: Optional[RangeInfo]

    def to_dict(self) -> Dict:
        d = {
            "support": self.support,
            "resistance": self.resistance,
        }
        if self.range is not None:
            d["range"] = self.range.to_dict()
        else:
            d["range"] = None
        return d


@dataclass
class Scores:
    trend_score: float
    vol_score: float
    momentum_score: float
    structure_score: float
    ta_bias: float
    directional_bias: str
    vol_bias: str

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TAResult:
    symbol: str
    timeframe_primary: Timeframe
    trend_regime: Regime
    vol_regime: Regime
    momentum: MomentumState
    structure: StructureInfo
    patterns: List[Dict]
    scores: Scores
    options_guidance: Dict

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timeframe_primary": self.timeframe_primary,
            "trend_regime": self.trend_regime.to_dict(),
            "vol_regime": self.vol_regime.to_dict(),
            "momentum": self.momentum.to_dict(),
            "structure": self.structure.to_dict(),
            "patterns": self.patterns,
            "scores": self.scores.to_dict(),
            "options_guidance": self.options_guidance,
        }


# ---------- Indicator utilities ----------


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(
    series: pd.Series,
    length: int = 20,
    std_mult: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ma = series.rolling(length).mean()
    std = series.rolling(length).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    return lower, ma, upper


def bollinger_bandwidth(
    series: pd.Series,
    length: int = 20,
    std_mult: float = 2.0,
) -> pd.Series:
    lower, ma, upper = bollinger_bands(series, length, std_mult)
    width = (upper - lower) / (ma + 1e-9)
    return width


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    # Simplified ADX implementation; good enough for regime classification
    high = df["high"]
    low = df["low"]

    plus_dm = high.diff()
    minus_dm = low.diff().abs()

    # Ensure these are 1D numpy arrays for np.where
    plus_dm_arr = np.asarray(plus_dm).reshape(-1)
    minus_dm_arr = np.asarray(minus_dm).reshape(-1)

    plus_dm_arr = np.where(
        (plus_dm_arr > minus_dm_arr) & (plus_dm_arr > 0),
        plus_dm_arr,
        0.0,
    )
    minus_dm_arr = np.where(
        (minus_dm_arr > plus_dm_arr) & (minus_dm_arr > 0),
        minus_dm_arr,
        0.0,
    )

    tr = true_range(df)
    atr_n = tr.rolling(period).mean()

    plus_dm_series = pd.Series(plus_dm_arr, index=df.index)
    minus_dm_series = pd.Series(minus_dm_arr, index=df.index)

    plus_di = 100 * (plus_dm_series.rolling(period).sum() / (atr_n + 1e-9))
    minus_di = 100 * (minus_dm_series.rolling(period).sum() / (atr_n + 1e-9))

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9) * 100
    adx_val = dx.rolling(period).mean()
    return adx_val


# ---------- Level and pattern helpers ----------


def _find_swing_points(
    df: pd.DataFrame,
    window: int = 5,
) -> Tuple[List[Tuple[pd.Timestamp, float]], List[Tuple[pd.Timestamp, float]]]:
    highs = df["high"]
    lows = df["low"]
    swing_highs = []
    swing_lows = []
    for i in range(window, len(df) - window):
        window_high = highs.iloc[i - window : i + window + 1]
        window_low = lows.iloc[i - window : i + window + 1]
        if highs.iloc[i] == window_high.max():
            swing_highs.append((df.index[i], highs.iloc[i]))
        if lows.iloc[i] == window_low.min():
            swing_lows.append((df.index[i], lows.iloc[i]))
    return swing_lows, swing_highs


def _cluster_levels(levels: List[float], tolerance: float = 0.002) -> List[float]:
    if not levels:
        return []
    levels = sorted(levels)
    clustered = []
    cluster = [levels[0]]
    for lvl in levels[1:]:
        if abs(lvl - cluster[-1]) / cluster[-1] <= tolerance:
            cluster.append(lvl)
        else:
            clustered.append(sum(cluster) / len(cluster))
            cluster = [lvl]
    clustered.append(sum(cluster) / len(cluster))
    return clustered


def detect_structure(df: pd.DataFrame, lookback: int = 120) -> StructureInfo:
    if len(df) < 20:
        return StructureInfo(support=[], resistance=[], range=None)

    df_lookback = df.iloc[-lookback:]
    swing_lows, swing_highs = _find_swing_points(df_lookback, window=3)
    low_levels = [lvl for _, lvl in swing_lows]
    high_levels = [lvl for _, lvl in swing_highs]

    support = _cluster_levels(low_levels)
    resistance = _cluster_levels(high_levels)

    recent = df_lookback
    range_low = recent["low"].min()
    range_high = recent["high"].max()
    close = recent["close"].iloc[-1]
    width = range_high - range_low
    if width <= 0:
        range_info = None
    else:
        in_range = (close >= range_low) and (close <= range_high)
        position_in_range = (close - range_low) / width
        range_info = RangeInfo(
            low=range_low,
            high=range_high,
            in_range=in_range,
            position_in_range=float(position_in_range),
        )

    return StructureInfo(
        support=support,
        resistance=resistance,
        range=range_info,
    )


def detect_simple_patterns(df: pd.DataFrame) -> List[Dict]:
    # Lightweight, non-exhaustive pattern detection.
    patterns: List[Dict] = []
    if len(df) < 3:
        return patterns

    last = df.iloc[-1]
    prev = df.iloc[-2]

    body_last = abs(last["close"] - last["open"])
    range_last = last["high"] - last["low"]
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]

    # Hammer-like candle near lows
    if range_last > 0 and lower_wick > 2 * body_last and upper_wick < body_last:
        patterns.append({"type": "hammer_like", "confidence": 0.6})

    # Shooting-star-like candle near highs
    if range_last > 0 and upper_wick > 2 * body_last and lower_wick < body_last:
        patterns.append({"type": "shooting_star_like", "confidence": 0.6})

    # Inside bar
    if (last["high"] <= prev["high"]) and (last["low"] >= prev["low"]):
        patterns.append({"type": "inside_bar", "confidence": 0.5})

    return patterns


# ---------- Regime & scoring logic ----------


def classify_trend_regime(df: pd.DataFrame) -> Regime:
    if len(df) < 60:
        return Regime(state="unknown", confidence=0.0)

    close = df["close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    adx_val = adx(df, 14)

    ema20_last = ema20.iloc[-1]
    ema50_last = ema50.iloc[-1]
    close_last = close.iloc[-1]
    adx_last = adx_val.iloc[-1]

    ema_slope = ema20.diff().iloc[-5:].mean()

    if np.isnan(adx_last):
        return Regime(state="unknown", confidence=0.0)

    if adx_last > 20:
        if close_last > ema20_last > ema50_last and ema_slope > 0:
            return Regime(state="uptrend", confidence=float(min(1.0, (adx_last - 20) / 20)))
        if close_last < ema20_last < ema50_last and ema_slope < 0:
            return Regime(state="downtrend", confidence=float(min(1.0, (adx_last - 20) / 20)))
        # strong but messy
        return Regime(state="choppy_trend", confidence=0.5)
    else:
        if abs(ema_slope) < (close_last * 0.0005):
            return Regime(state="range", confidence=0.7)
        return Regime(state="chop", confidence=0.5)


def classify_vol_regime(df: pd.DataFrame) -> Regime:
    if len(df) < 60:
        return Regime(state="unknown", confidence=0.0)

    close = df["close"]
    atr_val = atr(df, 14)
    atr_pct = atr_val / (close + 1e-9)
    bbw = bollinger_bandwidth(close, 20, 2.0)

    bbw_ma = bbw.rolling(50).mean()
    bbw_last = bbw.iloc[-1]
    bbw_ma_last = bbw_ma.iloc[-1]

    atr_pct_med = atr_pct.rolling(50).median().iloc[-1]
    atr_pct_last = atr_pct.iloc[-1]

    if np.isnan(bbw_last) or np.isnan(bbw_ma_last) or np.isnan(atr_pct_med):
        return Regime(state="unknown", confidence=0.0)

    # Simple compression / expansion logic
    compression = (bbw_last < 0.9 * bbw_ma_last) and (atr_pct_last < atr_pct_med)
    expansion = (bbw_last > 1.1 * bbw_ma_last) and (atr_pct_last > atr_pct_med)

    if compression:
        # stronger confidence the further below the average
        diff = (bbw_ma_last - bbw_last) / (bbw_ma_last + 1e-9)
        conf = float(max(0.5, min(1.0, diff)))
        return Regime(state="compression", confidence=conf)

    if expansion:
        diff = (bbw_last - bbw_ma_last) / (bbw_ma_last + 1e-9)
        conf = float(max(0.5, min(1.0, diff)))
        return Regime(state="expansion", confidence=conf)

    return Regime(state="normal", confidence=0.5)


def compute_momentum_state(df: pd.DataFrame) -> MomentumState:
    close = df["close"]
    rsi_series = rsi(close, 14)
    _, _, hist = macd(close, 12, 26, 9)

    rsi_val = float(rsi_series.iloc[-1])
    rsi_slope = float(rsi_series.diff().iloc[-4:].mean())
    hist_val = float(hist.iloc[-1])
    hist_slope = float(hist.diff().iloc[-4:].mean())

    # classify momentum state
    if hist_val > 0 and hist_slope > 0:
        state = "up_accelerating"
    elif hist_val > 0 and hist_slope < 0:
        state = "up_fading"
    elif hist_val < 0 and hist_slope < 0:
        state = "down_accelerating"
    elif hist_val < 0 and hist_slope > 0:
        state = "down_fading"
    else:
        state = "neutral"

    return MomentumState(
        state=state,
        rsi=rsi_val,
        rsi_slope=rsi_slope,
        macd_hist=hist_val,
        macd_hist_slope=hist_slope,
    )


def compute_scores(
    trend_regime: Regime,
    vol_regime: Regime,
    momentum: MomentumState,
    structure: StructureInfo,
    strategy_hint: Optional[str] = None,
) -> Scores:
    # Base scores in [-1, 1]
    trend_score = 0.0
    if trend_regime.state == "uptrend":
        trend_score = 0.7 * trend_regime.confidence
    elif trend_regime.state == "downtrend":
        trend_score = -0.7 * trend_regime.confidence
    elif trend_regime.state in ("range", "chop"):
        trend_score = 0.0
    elif trend_regime.state == "choppy_trend":
        trend_score = 0.2 * np.sign(trend_regime.confidence)

    # Vol score: positive means expansion bias (used more as vol_bias than direction)
    if vol_regime.state in ("compression", "expansion"):
        vol_score = 0.8 * vol_regime.confidence
    elif vol_regime.state == "normal":
        vol_score = 0.0
    else:
        vol_score = 0.0

    # Momentum score
    momentum_score = 0.0
    if momentum.state == "up_accelerating":
        momentum_score = 0.7
    elif momentum.state == "up_fading":
        momentum_score = 0.3
    elif momentum.state == "down_accelerating":
        momentum_score = -0.7
    elif momentum.state == "down_fading":
        momentum_score = -0.3

    # Structure score: we bias positive if near lower range, negative if near upper
    structure_score = 0.0
    if structure.range is not None and structure.range.in_range:
        pos = structure.range.position_in_range
        if pos < 0.25:
            structure_score = 0.4
        elif pos > 0.75:
            structure_score = -0.4
        else:
            structure_score = 0.0

    # Strategy-aware weighting
    w_trend, w_vol, w_momo, w_struct = 0.3, 0.3, 0.2, 0.2  # neutral default

    if strategy_hint == "short_premium_range":
        w_trend, w_vol, w_momo, w_struct = 0.2, 0.4, 0.1, 0.3
    elif strategy_hint == "short_premium_trend":
        w_trend, w_vol, w_momo, w_struct = 0.4, 0.2, 0.2, 0.2
    elif strategy_hint == "long_premium_breakout":
        w_trend, w_vol, w_momo, w_struct = 0.2, 0.4, 0.3, 0.1

    ta_bias = (
        w_trend * trend_score
        + w_vol * vol_score
        + w_momo * momentum_score
        + w_struct * structure_score
    )
    ta_bias = float(max(-1.0, min(1.0, ta_bias)))

    if ta_bias > 0.4:
        directional_bias = "bullish"
    elif ta_bias > 0.1:
        directional_bias = "slightly_bullish"
    elif ta_bias < -0.4:
        directional_bias = "bearish"
    elif ta_bias < -0.1:
        directional_bias = "slightly_bearish"
    else:
        directional_bias = "neutral"

    if vol_regime.state == "compression":
        vol_bias = "expansion_likely"
    elif vol_regime.state == "expansion":
        vol_bias = "elevated"
    else:
        vol_bias = "normal"

    return Scores(
        trend_score=float(trend_score),
        vol_score=float(vol_score),
        momentum_score=float(momentum_score),
        structure_score=float(structure_score),
        ta_bias=ta_bias,
        directional_bias=directional_bias,
        vol_bias=vol_bias,
    )


def _suggest_options_guidance(
    symbol: str,
    trend_regime: Regime,
    vol_regime: Regime,
    momentum: MomentumState,
    structure: StructureInfo,
    scores: Scores,
) -> Dict:
    preferred: List[str] = []
    notes: List[str] = []

    # Range / neutral stuff
    if trend_regime.state in ("range", "chop") and vol_regime.state in ("compression", "normal"):
        preferred.append("short_premium_range")
        notes.append(
            "Price appears to be in a range with non-extreme volatility – candidate for iron condors "
            "or short strangles, depending on IV."
        )

    # Directional hints
    if scores.directional_bias in ("bullish", "slightly_bullish"):
        preferred.append("short_premium_trend_bullish")
        notes.append("Directional bullish bias – consider put credit spreads or bullish diagonals depending on IV/IVR.")
    if scores.directional_bias in ("bearish", "slightly_bearish"):
        preferred.append("short_premium_trend_bearish")
        notes.append("Directional bearish bias – consider call credit spreads or bearish diagonals depending on IV/IVR.")

    # Vol compression → long premium setups
    if vol_regime.state == "compression":
        preferred.append("long_premium_breakout")
        notes.append(
            "Volatility compression suggests potential future expansion – consider calendars, diagonals, or debit "
            "spreads around key levels."
        )

    if structure.range is not None:
        r = structure.range
        if r.in_range:
            if r.position_in_range > 0.7:
                notes.append(
                    "Price trades near recent range highs – call spreads or IC call wing placement above resistance "
                    "may be safer."
                )
            elif r.position_in_range < 0.3:
                notes.append(
                    "Price trades near recent range lows – put spreads or IC put wing placement below support "
                    "may be safer."
                )

    return {
        "preferred_setups": preferred,
        "notes": notes,
    }


# ---------- Data access + main engine ----------


class ChartistEngine:
    """
    Lightweight technical analysis engine intended for use by the StratDeck ChartistAgent.

    It does not assume any particular data backend. If `data_client` is not provided,
    and STRATDECK_DATA_MODE != 'mock', it will fall back to yfinance where available.
    """

    def __init__(self, data_client=None, mode: Optional[str] = None):
        self.data_client = data_client
        self.mode = mode or os.getenv("STRATDECK_DATA_MODE", "mock")

    def _map_symbol_for_data(self, symbol: str) -> str:
        """
        Map StratDeck symbols to data-provider symbols for OHLCV.

        This ONLY affects how we fetch candles, not how we label TAResult.
        """
        s = symbol.upper()

        # Index mapping (yfinance quirks)
        alias_map = {
            "SPX": "^GSPC",   # S&P 500 index
            "XSP": "^GSPC",   # mini SPX – structurally same index; scale is fine for TA
        }

        return alias_map.get(s, symbol)

    # ---- public API ----

    def analyze(
        self,
        symbol: str,
        timeframes: Tuple[Timeframe, ...] = ("30m", "1h", "1d"),
        strategy_hint: Optional[str] = None,
        lookback_bars: int = 200,
    ) -> TAResult:
        # For now we run full logic on the *shortest* timeframe and use others later if needed
        primary_tf = timeframes[0]
        df = self._get_ohlcv(symbol, primary_tf, lookback_bars)

        if df is None or df.empty:
            raise ValueError(f"No OHLCV data available for {symbol} @ {primary_tf}")

        df = df.sort_index()
        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
        )

        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                raise ValueError(f"Missing column '{col}' in OHLCV for {symbol} @ {primary_tf}")

        trend_regime = classify_trend_regime(df)
        vol_regime = classify_vol_regime(df)
        momentum = compute_momentum_state(df)
        structure = detect_structure(df)
        patterns = detect_simple_patterns(df)
        scores = compute_scores(
            trend_regime=trend_regime,
            vol_regime=vol_regime,
            momentum=momentum,
            structure=structure,
            strategy_hint=strategy_hint,
        )
        options_guidance = _suggest_options_guidance(
            symbol=symbol,
            trend_regime=trend_regime,
            vol_regime=vol_regime,
            momentum=momentum,
            structure=structure,
            scores=scores,
        )

        return TAResult(
            symbol=symbol,
            timeframe_primary=primary_tf,
            trend_regime=trend_regime,
            vol_regime=vol_regime,
            momentum=momentum,
            structure=structure,
            patterns=patterns,
            scores=scores,
            options_guidance=options_guidance,
        )

    # ---- internals ----

    def _get_ohlcv(self, symbol: str, timeframe: Timeframe, lookback_bars: int) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data for the symbol/timeframe.
        """
        if self.data_client is not None and hasattr(self.data_client, "get_ohlcv"):
            return self.data_client.get_ohlcv(symbol, timeframe, lookback_bars)

        if self.mode == "mock":
            return self._mock_ohlcv(symbol, lookback_bars)

        if yf is None:
            raise RuntimeError("No data_client provided, and yfinance is not installed.")

        # 🔹 NEW: map SPX/XSP → ^GSPC (or other aliases) for yfinance
        yf_symbol = self._map_symbol_for_data(symbol)

        interval = self._map_tf_to_yf_interval(timeframe)
        df = yf.download(
            yf_symbol,
            period="60d",
            interval=interval,
            auto_adjust=False,
            progress=False,
        )

        # If still nothing, fall back to synthetic (keep your existing warning)
        if df is None or df.empty:
            import warnings
            warnings.warn(
                f"ChartistEngine received no OHLCV data for {symbol} "
                f"(mapped to {yf_symbol}) from yfinance; using synthetic data instead."
            )
            return self._mock_ohlcv(symbol, lookback_bars)

        # Handle MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(yf_symbol, axis=1, level=-1)
            except Exception:
                df = df.droplevel(-1, axis=1)

        if len(df) > lookback_bars:
            df = df.iloc[-lookback_bars:]

        return df

    def _map_tf_to_yf_interval(self, timeframe: Timeframe) -> str:
        mapping = {
            "1m": "1m",
            "2m": "2m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "60m": "60m",
            "1h": "60m",
            "1d": "1d",
            "D": "1d",
        }
        return mapping.get(timeframe, "1d")

    def _mock_ohlcv(self, symbol: str, lookback_bars: int) -> pd.DataFrame:
        """
        Generate synthetic OHLCV for testing wired flows without a real data source.
        """
        rng = pd.date_range(end=pd.Timestamp.utcnow(), periods=lookback_bars, freq="30min")
        base_price = 100.0
        noise = np.random.normal(0, 0.5, size=lookback_bars).cumsum()
        trend = np.linspace(-1, 1, lookback_bars)
        price = base_price + trend + noise

        high = price + np.random.uniform(0.2, 0.8, size=lookback_bars)
        low = price - np.random.uniform(0.2, 0.8, size=lookback_bars)
        open_ = price + np.random.uniform(-0.3, 0.3, size=lookback_bars)
        close = price + np.random.uniform(-0.3, 0.3, size=lookback_bars)
        volume = np.random.randint(1_000, 10_000, size=lookback_bars)

        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=rng,
        )
        return df

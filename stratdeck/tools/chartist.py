# stratdeck/agents/chartist.py

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from stratdeck.tools.ta import ChartistEngine, TAResult

LLMClient = Callable[..., Any]


def _project_root() -> Path:
    """
    Best-effort guess at the project root:
    stratdeck/agents/chartist.py → stratdeck → <root>.
    """
    here = Path(__file__).resolve()
    return here.parents[2]


def _load_prompt_file(name: str) -> str:
    """
    Load a prompt file from conf/prompts/<name>.
    If it's missing, return a built-in default.
    """
    root = _project_root()
    path = root / "conf" / "prompts" / name
    if path.is_file():
        return path.read_text(encoding="utf-8")

    # Built-in fallback defaults
    if name == "chartist_system.md":
        return (
            "You are the ChartistAgent in the StratDeck system.\n\n"
            "You receive:\n"
            "- A JSON object called TA_RESULT produced by a deterministic technical analysis engine.\n"
            "- Optional context about the intended options strategy.\n\n"
            "Rules:\n"
            "- Treat TA_RESULT as factual for indicator values, regimes, levels, and scores.\n"
            "- Do not invent or guess new indicator readings that are not present in TA_RESULT.\n"
            "- Focus on how trend, volatility regime, momentum, and structure affect options strategies,\n"
            "  especially short premium, long premium, and strike selection.\n"
            "- Your goal is to provide concise, actionable technical guidance that downstream agents\n"
            "  (TraderAgent, RiskAgent, JournalAgent) can use.\n"
        )
    if name == "chartist_report.md":
        return (
            "You are generating a short, actionable technical summary for a trader.\n\n"
            "You are given a JSON object TA_RESULT with keys such as:\n"
            "- trend_regime\n"
            "- vol_regime\n"
            "- momentum\n"
            "- structure (support/resistance/range)\n"
            "- scores (ta_bias, directional_bias, vol_bias)\n"
            "- options_guidance\n\n"
            "Task:\n"
            "- Produce 3–6 bullet points.\n"
            "- Summarise: trend, volatility regime, key levels, and what they imply for options positioning.\n"
            "- Be concrete about where strikes might be placed relative to support/resistance or ranges.\n"
            "- Do not restate raw JSON; interpret it.\n"
        )

    # Generic fallback
    return f"Prompt file {name} not found. Proceed with minimal context."


class ChartistAgent:
    """
    ChartistAgent: orchestrates technical analysis for StratDeck.

    Responsibilities:
    - Call ChartistEngine (ta.py) to compute TAResult for symbols.
    - Optionally, call an LLM with system/user prompts to generate human-readable summaries.
    - Provide enriched outputs for TraderAgent / RiskAgent that include TA metadata.

    LLM expectations:
    - `llm_client` should be a callable accepting `messages=[...]` and returning either:
        - a string, or
        - an object with a `.content` or similar that you can adapt.
      This is intentionally loose; adapt `_call_llm` to your actual client as needed.
    """

    def __init__(
        self,
        ta_engine: Optional[ChartistEngine] = None,
        llm_client: Optional[LLMClient] = None,
        prompts_dir: Optional[Path] = None,
    ) -> None:
        self.ta_engine = ta_engine or ChartistEngine()
        self.llm_client = llm_client
        self._prompts_dir = prompts_dir  # reserved if you ever want to override _load_prompt_file

    # ---------- Public API: TA-only layer ----------

    def analyze_symbol(
        self,
        symbol: str,
        strategy_hint: Optional[str] = None,
        timeframes: Tuple[str, ...] = ("30m", "1h", "1d"),
        lookback_bars: int = 200,
    ) -> TAResult:
        """
        Run the technical engine for a single symbol and return TAResult.
        Does not call any LLM.
        """
        return self.ta_engine.analyze(
            symbol=symbol,
            timeframes=timeframes,
            strategy_hint=strategy_hint,
            lookback_bars=lookback_bars,
        )

    def analyze_symbols(
        self,
        symbols: Sequence[str],
        strategy_hint: Optional[str] = None,
        timeframes: Tuple[str, ...] = ("30m", "1h", "1d"),
        lookback_bars: int = 200,
    ) -> Dict[str, TAResult]:
        """
        Run the technical engine across a batch of symbols.
        Returns a mapping {symbol: TAResult}.
        """
        results: Dict[str, TAResult] = {}
        for sym in symbols:
            results[sym] = self.analyze_symbol(
                symbol=sym,
                strategy_hint=strategy_hint,
                timeframes=timeframes,
                lookback_bars=lookback_bars,
            )
        return results

    def analyze_scout_batch(
        self,
        scout_results: Iterable[Dict[str, Any]],
        default_strategy_hint: Optional[str] = None,
        symbol_key: str = "symbol",
    ) -> List[Dict[str, Any]]:
        """
        Take in a batch of ScoutAgent outputs (list of dicts with at least `symbol`),
        attach TA metadata, and return a new enriched list suitable for TraderAgent.

        Each output row roughly looks like:
        {
            ... original scout fields ...,
            "ta": <TA_RESULT_DICT>,
            "ta_directional_bias": "...",
            "ta_vol_bias": "...",
            "ta_score": float,
        }

        If an individual scout row includes a 'strategy_hint' key, that overrides default_strategy_hint.
        """
        enriched: List[Dict[str, Any]] = []

        for row in scout_results:
            sym = row.get(symbol_key)
            if not sym:
                continue
            strategy_hint = row.get("strategy_hint", default_strategy_hint)

            ta_res = self.analyze_symbol(sym, strategy_hint=strategy_hint)
            ta_dict = ta_res.to_dict()

            out_row = dict(row)  # shallow copy
            out_row["ta"] = ta_dict
            out_row["ta_directional_bias"] = ta_dict["scores"]["directional_bias"]
            out_row["ta_vol_bias"] = ta_dict["scores"]["vol_bias"]
            out_row["ta_score"] = ta_dict["scores"]["ta_bias"]

            enriched.append(out_row)

        return enriched

    # ---------- Public API: LLM-enhanced summaries ----------

    def summarise_ta(
        self,
        ta_result: TAResult,
        extra_context: Optional[Dict[str, Any]] = None,
        llm_kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Use the LLM (if configured) to generate a short technical summary
        from a single TAResult, using chartist_system + chartist_report prompts.

        If no LLM client is configured, returns a basic plain-text summary.
        """
        if self.llm_client is None:
            return self._fallback_summary(ta_result)

        system_prompt = _load_prompt_file("chartist_system.md")
        user_prompt_template = _load_prompt_file("chartist_report.md")

        payload: Dict[str, Any] = {
            "TA_RESULT": ta_result.to_dict(),
        }
        if extra_context:
            payload["CONTEXT"] = extra_context

        user_prompt = (
            user_prompt_template
            + "\n\nHere is the TA_RESULT JSON:\n\n"
            + json.dumps(payload, indent=2)
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        llm_kwargs = llm_kwargs or {}
        return self._call_llm(messages=messages, **llm_kwargs)

    def summarise_batch(
        self,
        ta_results: Dict[str, TAResult],
        extra_context: Optional[Dict[str, Any]] = None,
        llm_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """
        Generate summaries for a batch of TAResults keyed by symbol.
        """
        summaries: Dict[str, str] = {}
        for sym, ta_res in ta_results.items():
            summaries[sym] = self.summarise_ta(
                ta_res,
                extra_context=extra_context,
                llm_kwargs=llm_kwargs,
            )
        return summaries

    # ---------- Internal helpers ----------

    def _call_llm(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        Very thin wrapper around llm_client. Adjust this to your actual LLM interface.
        """
        if self.llm_client is None:
            raise RuntimeError("LLM client not configured for ChartistAgent.")

        result = self.llm_client(messages=messages, **kwargs)

        # Try to normalise various possible return types.
        if isinstance(result, str):
            return result

        # If it's some structured object, try common patterns.
        for attr in ("content", "text"):
            if hasattr(result, attr):
                return getattr(result, attr)

        # OpenAI-style: {'choices': [{'message': {'content': '...'}}]}
        if isinstance(result, dict):
            try:
                return result["choices"][0]["message"]["content"]
            except Exception:
                pass

        # Fallback: stringification
        return str(result)

    def _fallback_summary(self, ta_result: TAResult) -> str:
        """
        Simple non-LLM summary, in case no llm_client is configured.
        """
        d = ta_result.to_dict()
        trend = d["trend_regime"]["state"]
        vol_state = d["vol_regime"]["state"]
        dir_bias = d["scores"]["directional_bias"]
        vol_bias = d["scores"]["vol_bias"]
        support = d["structure"]["support"]
        resistance = d["structure"]["resistance"]
        notes = d["options_guidance"].get("notes", [])

        lines = [
            f"Symbol: {d['symbol']}",
            f"Primary timeframe: {d['timeframe_primary']}",
            f"Trend regime: {trend}",
            f"Volatility regime: {vol_state} (vol_bias={vol_bias})",
            f"Directional bias: {dir_bias} (ta_bias={d['scores']['ta_bias']:.2f})",
        ]
        if support:
            lines.append(f"Support levels: {', '.join(f'{lvl:.2f}' for lvl in support)}")
        if resistance:
            lines.append(f"Resistance levels: {', '.join(f'{lvl:.2f}' for lvl in resistance)}")
        if notes:
            lines.append("Options guidance:")
            for n in notes:
                lines.append(f"- {n}")

        return "\n".join(lines)
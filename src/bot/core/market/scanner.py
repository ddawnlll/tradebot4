"""Cross-sectional scanner: liquidity ranking and range-candidate assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bot.core.market.scoring import RangeScoreConfig, build_range_setup
from bot.core.market.state import SymbolState
from bot.core.models import Bar, MarketRegime, RangeSetup


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    min_quote_volume_24h: float = 5_000_000.0
    min_liquidity_score: float = 0.05
    liquidity_log_floor: float = 1_000_000.0


class MarketScanner:
    def __init__(self, config: ScannerConfig | None = None, score_config: RangeScoreConfig | None = None) -> None:
        self.config = config or ScannerConfig()
        self.score_config = score_config or RangeScoreConfig()
        self._liq_ts: int = -1
        self._liq: dict[str, float] = {}

    def liquidity_scores(self, states: Mapping[str, SymbolState], ts_ms: int) -> dict[str, float]:
        if ts_ms == self._liq_ts:
            return self._liq
        volumes = {s: st.quote_volume_24h for s, st in states.items()}
        if len(volumes) == 1:
            self._liq = {next(iter(volumes)): 1.0}
            self._liq_ts = ts_ms
            return self._liq
        ranked = sorted(volumes.items(), key=lambda kv: kv[1])
        n = max(len(ranked) - 1, 1)
        self._liq = {s: i / n for i, (s, _) in enumerate(ranked)}
        self._liq_ts = ts_ms
        return self._liq

    def liquidity_score(self, state: SymbolState, states: Mapping[str, SymbolState]) -> float:
        scores = self.liquidity_scores(states, state.last_ts_ms)
        base = scores.get(state.symbol, 0.0)
        if state.quote_volume_24h < self.config.min_quote_volume_24h:
            return 0.0
        return base

    def build_setup(
        self,
        state: SymbolState,
        bar: Bar,
        regime: MarketRegime,
        liquidity_score: float,
    ) -> RangeSetup | None:
        if liquidity_score < self.config.min_liquidity_score:
            return None
        return build_range_setup(state, bar, regime, liquidity_score, self.score_config)

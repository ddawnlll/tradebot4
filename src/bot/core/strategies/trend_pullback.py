"""Benchmark: trend pullback continuation.

Purpose is not to be traded; it is the "does the range filter add value"
control. It uses the same risk, costs and execution adapter as the range
strategy so the comparison is apples to apples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.core.indicators.atr import EMA
from bot.core.models import RejectReason, RegimeState, Side
from bot.core.strategies.base import (
    EntryContext,
    EntryDecision,
    EntryProposal,
    ExitContext,
    ExitSignal,
    StrategyBase,
)


@dataclass(frozen=True, slots=True)
class TrendPullbackConfig:
    ema_period: int = 20
    stop_atr: float = 1.5
    tp_r_multiples: tuple[float, ...] = (2.0, 3.0)
    min_regime_confidence: float = 0.2
    max_extension_atr: float = 2.5


class TrendPullbackStrategy(StrategyBase):
    name = "trend_pullback"
    version = "1.0.0"

    def __init__(self, config: TrendPullbackConfig | None = None) -> None:
        self.config = config or TrendPullbackConfig()
        self._emas: dict[str, EMA] = {}

    def config_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, **self.config.__dict__}

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        c = self.config
        regime = ctx.regime
        if regime.state not in (RegimeState.TREND_UP, RegimeState.TREND_DOWN):
            return EntryDecision(None, RejectReason.REGIME, regime.state.value)
        if regime.confidence < c.min_regime_confidence:
            return EntryDecision(None, RejectReason.REGIME, "low confidence")
        atr = ctx.state.atr
        if atr is None or atr <= 0:
            return EntryDecision(None, RejectReason.MISSING_CONTEXT, "no atr")
        ema = self._emas.setdefault(ctx.state.symbol, EMA(c.ema_period))
        value = ema.update(ctx.bar.close)
        if value is None:
            return EntryDecision(None, RejectReason.MISSING_CONTEXT, "ema warmup")

        bar = ctx.bar
        extension = abs(bar.close - value) / atr
        if extension > c.max_extension_atr:
            return EntryDecision(None, RejectReason.MOMENTUM, "extended")

        if regime.state is RegimeState.TREND_UP:
            pullback = bar.low <= value and bar.close > value and bar.is_bullish
            if not pullback:
                return EntryDecision(None, RejectReason.NOT_AT_EDGE, "no pullback")
            stop = bar.low - 0.5 * atr
            if stop >= bar.close:
                return EntryDecision(None, RejectReason.NO_RANGE, "bad stop")
            tps = tuple(bar.close + (bar.close - stop) * m for m in c.tp_r_multiples)
            return EntryDecision(
                EntryProposal(
                    side=Side.LONG,
                    entry_price=bar.close,
                    stop_price=stop,
                    take_profits=tps,
                    reason="trend_pullback_long",
                )
            )
        pullback = bar.high >= value and bar.close < value and bar.is_bearish
        if not pullback:
            return EntryDecision(None, RejectReason.NOT_AT_EDGE, "no pullback")
        stop = bar.high + 0.5 * atr
        if stop <= bar.close:
            return EntryDecision(None, RejectReason.NO_RANGE, "bad stop")
        tps = tuple(bar.close - (stop - bar.close) * m for m in c.tp_r_multiples)
        return EntryDecision(
            EntryProposal(
                side=Side.SHORT,
                entry_price=bar.close,
                stop_price=stop,
                take_profits=tps,
                reason="trend_pullback_short",
            )
        )

    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None:
        return None

"""Range mean-reversion strategy, variant 1: confirmed reversal at the edge.

Entry (long)
------------
1. 1h regime is RANGE with enough confidence and low breakout/squeeze risk.
2. A validated range exists (>= 2 touches per side, width vs cost acceptable).
3. The 15m bar trades into the support zone (``low <= support.upper``) and
   closes back inside the range above the zone mid.
4. The bar shows rejection (lower wick) and is not an impulsive breakout bar.
5. Volume is not a spike relative to the recent median.

Short is symmetric at the resistance zone.

The stop is placed beyond the zone's outer edge plus an empirical sweep buffer,
so a wick through the level does not automatically stop the trade. TP1 is the
range midpoint; TP2 is just inside the opposite zone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
class RangeV1Config:
    min_regime_confidence: float = 0.15
    max_breakout_risk: float = 0.70
    max_squeeze_risk: float = 0.85
    max_body_atr: float = 0.90
    max_bar_range_atr: float = 1.8
    rejection_wick_frac: float = 0.25
    vol_spike_mult: float = 4.0
    tp2_offset_frac: float = 0.10
    min_stop_pct: float = 0.0035
    max_stop_pct: float = 0.06
    respect_higher_tf: bool = True
    entry_tf_bars_min: int = 60
    breakout_exit_atr: float = 0.35
    breakout_exit_vol_mult: float = 2.5
    time_stop_bars: int = 32  # 8 hours on 15m decisions, measured in 15m bars
    weights: dict[str, float] = field(default_factory=dict)


class RangeV1Strategy(StrategyBase):
    name = "range_v1"
    version = "1.0.0"

    def __init__(self, config: RangeV1Config | None = None) -> None:
        self.config = config or RangeV1Config()

    def config_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, **self.config.__dict__}

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        c = self.config
        regime = ctx.regime
        if regime.state is not RegimeState.RANGE:
            return EntryDecision(None, RejectReason.REGIME, regime.state.value)
        if regime.confidence < c.min_regime_confidence:
            return EntryDecision(None, RejectReason.REGIME, "low regime confidence")
        if regime.breakout_risk > c.max_breakout_risk:
            return EntryDecision(None, RejectReason.BREAKOUT_RISK, f"{regime.breakout_risk:.2f}")
        if regime.squeeze_risk > c.max_squeeze_risk:
            return EntryDecision(None, RejectReason.SQUEEZE_RISK, f"{regime.squeeze_risk:.2f}")
        if len(ctx.state.bars_entry) < c.entry_tf_bars_min:
            return EntryDecision(None, RejectReason.MISSING_CONTEXT, "warmup")

        setup = ctx.setup
        if setup is None:
            return EntryDecision(None, RejectReason.NO_RANGE, "no validated range")
        bar = ctx.bar
        atr = setup.atr
        if atr <= 0:
            return EntryDecision(None, RejectReason.MISSING_CONTEXT, "no atr")

        if bar.range > c.max_bar_range_atr * atr or abs(bar.body) > c.max_body_atr * atr:
            return EntryDecision(None, RejectReason.MOMENTUM, "impulse bar")
        median_qv = ctx.state.median_entry_quote_volume
        if median_qv > 0 and bar.quote_volume > c.vol_spike_mult * median_qv:
            return EntryDecision(None, RejectReason.VOLUME_SPIKE, "volume spike")

        long_ok = self._long_trigger(ctx, setup)
        short_ok = self._short_trigger(ctx, setup)
        if long_ok and short_ok:
            long_ok = bar.close <= setup.mid
            short_ok = not long_ok
        if long_ok:
            proposal = self._long_proposal(ctx, setup)
            if isinstance(proposal, EntryDecision):
                return proposal
            return EntryDecision(proposal)
        if short_ok:
            proposal = self._short_proposal(ctx, setup)
            if isinstance(proposal, EntryDecision):
                return proposal
            return EntryDecision(proposal)
        return EntryDecision(None, RejectReason.NOT_AT_EDGE, "price not at zone with rejection")

    # ------------------------------------------------------------------ sides
    def _long_trigger(self, ctx: EntryContext, setup) -> bool:
        bar = ctx.bar
        support = setup.support
        touched = bar.low <= support.upper
        inside = bar.close >= support.center and bar.close < setup.resistance.lower
        rejection = bar.lower_wick >= self.config.rejection_wick_frac * max(bar.range, 1e-12)
        if not (touched and inside and rejection):
            return False
        if self.config.respect_higher_tf and ctx.higher_regime is not None:
            if ctx.higher_regime.state is RegimeState.TREND_DOWN:
                return False
        return True

    def _short_trigger(self, ctx: EntryContext, setup) -> bool:
        bar = ctx.bar
        res = setup.resistance
        touched = bar.high >= res.lower
        inside = bar.close <= res.center and bar.close > setup.support.upper
        rejection = bar.upper_wick >= self.config.rejection_wick_frac * max(bar.range, 1e-12)
        if not (touched and inside and rejection):
            return False
        if self.config.respect_higher_tf and ctx.higher_regime is not None:
            if ctx.higher_regime.state is RegimeState.TREND_UP:
                return False
        return True

    def _long_proposal(self, ctx: EntryContext, setup) -> EntryProposal | EntryDecision:
        price = ctx.bar.close
        stop = setup.support.lower - self._buffer(ctx, setup)
        tp1 = setup.mid
        tp2 = setup.resistance.lower - self.config.tp2_offset_frac * setup.width
        if stop >= price:
            return EntryDecision(None, RejectReason.NO_RANGE, "stop above entry")
        if not (price < tp1 < tp2):
            return EntryDecision(None, RejectReason.NO_RANGE, "invalid tp ladder")
        stop_pct = (price - stop) / price
        if stop_pct < self.config.min_stop_pct:
            return EntryDecision(None, RejectReason.HIGH_COST, "stop too tight")
        if stop_pct > self.config.max_stop_pct:
            return EntryDecision(None, RejectReason.NARROW_RANGE, "stop too wide")
        return EntryProposal(
            side=Side.LONG,
            entry_price=price,
            stop_price=stop,
            take_profits=(tp1, tp2),
            reason="range_support_rejection",
            features={"setup": setup, "stop_pct": stop_pct},
        )

    def _short_proposal(self, ctx: EntryContext, setup) -> EntryProposal | EntryDecision:
        price = ctx.bar.close
        stop = setup.resistance.upper + self._buffer(ctx, setup)
        tp1 = setup.mid
        tp2 = setup.support.upper + self.config.tp2_offset_frac * setup.width
        if stop <= price:
            return EntryDecision(None, RejectReason.NO_RANGE, "stop below entry")
        if not (price > tp1 > tp2):
            return EntryDecision(None, RejectReason.NO_RANGE, "invalid tp ladder")
        stop_pct = (stop - price) / price
        if stop_pct < self.config.min_stop_pct:
            return EntryDecision(None, RejectReason.HIGH_COST, "stop too tight")
        if stop_pct > self.config.max_stop_pct:
            return EntryDecision(None, RejectReason.NARROW_RANGE, "stop too wide")
        return EntryProposal(
            side=Side.SHORT,
            entry_price=price,
            stop_price=stop,
            take_profits=(tp1, tp2),
            reason="range_resistance_rejection",
            features={"setup": setup, "stop_pct": stop_pct},
        )

    def _buffer(self, ctx: EntryContext, setup) -> float:
        atr = setup.atr
        fallback = 0.35 * atr
        if ctx.state.last_1m is not None:
            fallback = max(fallback, 0.0015 * ctx.state.last_1m.close)
        return ctx.state.zones.sweep_buffer(0.9, fallback=fallback)

    # ------------------------------------------------------------------- exit
    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None:
        """Signal-based exits. Native stop/TP orders are handled by the adapter."""
        return None


class RangeV2PassiveStrategy(RangeV1Strategy):
    """Variant 2: post-only limit at the inner edge of the zone.

    Kept as a separate strategy id so fill-rate and adverse-selection
    statistics never mix with V1 market entries.
    """

    name = "range_v2_passive"
    version = "1.0.0"

    def __init__(self, config: RangeV1Config | None = None) -> None:
        super().__init__(config)

"""Range quality scoring.

The score is a *pre-registered hypothesis*, not a validated predictor. Its
components and weights are frozen before any out-of-sample evaluation and its
calibration is measured afterwards (score deciles vs net R). Do not tune the
weights against the test period.

Component ranges are normalized to [0, 1] so weights are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.core.market.state import SymbolState
from bot.core.models import Bar, MarketRegime, RangeSetup, Zone


@dataclass(frozen=True, slots=True)
class RangeScoreConfig:
    min_width_pct: float = 0.008
    max_width_pct: float = 0.20
    min_touches: int = 2
    min_width_cost_ratio: float = 3.0
    target_width_cost_ratio: float = 8.0
    max_zone_distance_atr: float = 3.5
    stop_buffer_atr: float = 0.35
    min_stop_buffer_pct: float = 0.0015
    duration_target_bars: float = 240.0
    min_score: float = 0.55
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "duration": 0.10,
            "touches": 0.15,
            "width_cost": 0.15,
            "location": 0.10,
            "liquidity": 0.15,
            "regime": 0.15,
            "breakout": 0.10,
            "squeeze": 0.10,
        }
    )


def build_range_setup(
    state: SymbolState,
    bar: Bar,
    regime: MarketRegime,
    liquidity_score: float,
    cfg: RangeScoreConfig | None = None,
) -> RangeSetup | None:
    """Select the tightest valid support/resistance pair around price.

    Returns ``None`` when no pair qualifies. The chosen pair is the nearest one
    that satisfies touch, width and cost constraints; this keeps the trade's
    take-profit reachable and its stop structural.
    """
    cfg = cfg or RangeScoreConfig()
    atr = state.atr
    if atr is None or atr <= 0:
        return None
    price = bar.close
    max_dist = cfg.max_zone_distance_atr * atr

    supports = [z for z in state.zones.zones("SUPPORT", cfg.min_touches) if price - z.center > 0 and price - z.center <= max_dist]
    resistances = [z for z in state.zones.zones("RESISTANCE", cfg.min_touches) if z.center - price > 0 and z.center - price <= max_dist]
    if not supports or not resistances:
        return None
    supports.sort(key=lambda z: price - z.center)
    resistances.sort(key=lambda z: z.center - price)

    round_trip_cost = price * (2 * state.spec.taker_fee + 2 * 0.0002)  # fees + 2 bp/side slippage
    stop_buffer = _stop_buffer(state, atr, cfg)
    best: RangeSetup | None = None
    best_score = 0.0

    for support in supports[:3]:
        for resistance in resistances[:3]:
            width = resistance.center - support.center
            if width <= 0:
                continue
            width_pct = width / price
            if width_pct < cfg.min_width_pct or width_pct > cfg.max_width_pct:
                continue
            ratio = width / (round_trip_cost + stop_buffer)
            if ratio < cfg.min_width_cost_ratio:
                continue
            setup = _assemble(state, bar, regime, support, resistance, atr, width, width_pct, ratio, liquidity_score, stop_buffer, cfg)
            if setup.score > best_score:
                best, best_score = setup, setup.score

    if best is None or best.score < cfg.min_score:
        return None
    return best


def _assemble(
    state: SymbolState,
    bar: Bar,
    regime: MarketRegime,
    support: Zone,
    resistance: Zone,
    atr: float,
    width: float,
    width_pct: float,
    ratio: float,
    liquidity_score: float,
    stop_buffer: float,
    cfg: RangeScoreConfig,
) -> RangeSetup:
    tf = state.config.entry_tf_ms
    span_ms = max(resistance.last_touch_ms - support.first_touch_ms, 0)
    duration_bars = span_ms / tf
    min_touches = min(support.touches, resistance.touches)

    distance_to_edge = min(abs(bar.close - support.upper), abs(resistance.lower - bar.close))
    components = {
        "duration": _clamp(duration_bars / cfg.duration_target_bars),
        "touches": _clamp((min_touches - 1) / 4.0),
        "width_cost": _clamp((ratio - cfg.min_width_cost_ratio) / max(cfg.target_width_cost_ratio - cfg.min_width_cost_ratio, 1e-9)),
        "location": 1.0 - _clamp(distance_to_edge / max(0.5 * width, 1e-12)),
        "liquidity": _clamp(liquidity_score),
        "regime": _clamp(regime.confidence),
        "breakout": 1.0 - _clamp(regime.breakout_risk),
        "squeeze": 1.0 - _clamp(regime.squeeze_risk),
    }
    score = sum(cfg.weights[k] * components[k] for k in cfg.weights)
    return RangeSetup(
        symbol=state.symbol,
        support=support,
        resistance=resistance,
        mid=(support.center + resistance.center) / 2.0,
        width=width,
        width_pct=width_pct,
        duration_bars=int(duration_bars),
        atr=atr,
        breakout_risk=regime.breakout_risk,
        squeeze_risk=regime.squeeze_risk,
        score=score,
        score_components=components,
        ts_ms=bar.ts_close_ms,
    )


def _stop_buffer(state: SymbolState, atr: float, cfg: RangeScoreConfig) -> float:
    fallback = max(cfg.stop_buffer_atr * atr, cfg.min_stop_buffer_pct * (state.last_1m.close if state.last_1m else 0.0))
    return state.zones.sweep_buffer(0.9, fallback=fallback)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

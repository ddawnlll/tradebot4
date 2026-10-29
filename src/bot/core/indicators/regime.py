"""Regime classification from two complementary measures.

Choice of measures
------------------
1. **ADX(14)** - directional trend strength. It says whether a directional
   move exists, independent of its sign.
2. **Choppiness Index(14)** - how much of the window's range is traversed
   path-by-path (1 = pure trend, 100 = pure chop). It captures range
   compression/expansion that ADX misses.

They are deliberately complementary, not redundant: ADX is directional,
Choppiness is path-based. Bollinger bandwidth is used only as an input to
*breakout risk* (a squeeze precedes expansion); it is not a third regime vote.
Adding more indicators without evidence just multiplies degrees of freedom.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from bot.core.models import Bar, MarketRegime, RegimeState


class ADX:
    """Wilder ADX with +DI/-DI."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._prev: Bar | None = None
        self._tr = 0.0
        self._plus_dm = 0.0
        self._minus_dm = 0.0
        self._n = 0
        self._adx: float | None = None
        self._dx_values: deque[float] = deque(maxlen=period)
        self.plus_di: float | None = None
        self.minus_di: float | None = None

    @property
    def value(self) -> float | None:
        return self._adx

    @property
    def ready(self) -> bool:
        return self._adx is not None

    def update(self, bar: Bar) -> float | None:
        prev = self._prev
        self._prev = bar
        if prev is None:
            return None

        up_move = bar.high - prev.high
        down_move = prev.low - bar.low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))

        self._n += 1
        if self._n <= self.period:
            self._tr += tr
            self._plus_dm += plus_dm
            self._minus_dm += minus_dm
            if self._n < self.period:
                return None
        else:
            self._tr = self._tr - self._tr / self.period + tr
            self._plus_dm = self._plus_dm - self._plus_dm / self.period + plus_dm
            self._minus_dm = self._minus_dm - self._minus_dm / self.period + minus_dm

        if self._tr <= 0:
            return None
        self.plus_di = 100.0 * self._plus_dm / self._tr
        self.minus_di = 100.0 * self._minus_dm / self._tr
        denom = self.plus_di + self.minus_di
        dx = 100.0 * abs(self.plus_di - self.minus_di) / denom if denom > 0 else 0.0

        if self._adx is None:
            self._dx_values.append(dx)
            if len(self._dx_values) == self.period:
                self._adx = sum(self._dx_values) / self.period
        else:
            self._adx = (self._adx * (self.period - 1) + dx) / self.period
        return self._adx


class Choppiness:
    """Choppiness Index over closed bars: 100 * log10(sumTR / range) / log10(n)."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._bars: deque[Bar] = deque(maxlen=period)
        self._value: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, bar: Bar) -> float | None:
        self._bars.append(bar)
        if len(self._bars) < self.period:
            return None
        highs = max(b.high for b in self._bars)
        lows = min(b.low for b in self._bars)
        rng = highs - lows
        if rng <= 0:
            self._value = 50.0
            return self._value
        tr_sum = 0.0
        prev_close = self._bars[0].open
        for b in self._bars:
            tr_sum += max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
            prev_close = b.close
        import math

        self._value = 100.0 * math.log10(max(tr_sum / rng, 1e-12)) / math.log10(self.period)
        return self._value


class BandwidthPercentile:
    """Percentile rank of the current Bollinger bandwidth within a lookback window."""

    def __init__(self, bb_period: int = 20, lookback: int = 200, k: float = 2.0) -> None:
        self.bb_period = bb_period
        self.k = k
        self._closes: deque[float] = deque(maxlen=bb_period)
        self._history: deque[float] = deque(maxlen=lookback)
        self._current: float | None = None
        self._percentile: float | None = None

    @property
    def value(self) -> float | None:
        return self._percentile

    @property
    def ready(self) -> bool:
        return self._percentile is not None

    def update(self, bar: Bar) -> float | None:
        self._closes.append(bar.close)
        if len(self._closes) < self.bb_period:
            return None
        mean = sum(self._closes) / self.bb_period
        var = sum((c - mean) ** 2 for c in self._closes) / self.bb_period
        sd = var**0.5
        if mean <= 0:
            return None
        bw = (2 * self.k * sd) / mean
        self._current = bw
        if len(self._history) >= 20:
            below = sum(1 for v in self._history if v <= bw)
            self._percentile = below / len(self._history)
        self._history.append(bw)
        return self._percentile


@dataclass(slots=True)
class RegimeConfig:
    adx_period: int = 14
    chop_period: int = 14
    adx_range_max: float = 20.0
    adx_trend_min: float = 25.0
    chop_range_min: float = 55.0
    min_bars: int = 40


class RangeRegimeDetector:
    """Stateful regime detector fed with 1h (context) bars."""

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()
        self._adx = ADX(self.config.adx_period)
        self._chop = Choppiness(self.config.chop_period)
        self._bbw = BandwidthPercentile()
        self._state = RegimeState.UNKNOWN
        self._duration = 0
        self._bars = 0
        self._last: MarketRegime | None = None

    @property
    def state(self) -> RegimeState:
        return self._state

    @property
    def last(self) -> MarketRegime | None:
        return self._last

    def update(self, bar: Bar) -> MarketRegime:
        self._bars += 1
        adx = self._adx.update(bar)
        chop = self._chop.update(bar)
        bbw = self._bbw.update(bar)
        atr_proxy = bar.high - bar.low

        if adx is None or chop is None or self._bars < self.config.min_bars:
            state = RegimeState.UNKNOWN
            confidence = 0.0
        else:
            state = self._classify(adx, chop)
            confidence = self._confidence(state, adx, chop)

        if state is self._state and state is not RegimeState.UNKNOWN:
            self._duration += 1
        else:
            self._duration = 0
        self._state = state

        bbw = bbw if bbw is not None else 0.5
        # Squeeze risk is only *extreme* compression (bottom quintile of the
        # symbol's own bandwidth distribution). A tight-but-normal range is the
        # environment this strategy wants, so it must not be penalised.
        squeeze = _clamp((0.20 - bbw) / 0.20)
        breakout = (
            _clamp(0.5 * squeeze + 0.5 * _clamp((adx - 18.0) / 22.0)) if adx is not None else 0.5
        )
        regime = MarketRegime(
            state=state,
            confidence=confidence,
            duration_bars=self._duration,
            volatility=atr_proxy / bar.close if bar.close else 0.0,
            breakout_risk=breakout,
            adx=adx or 0.0,
            choppiness=chop or 0.0,
            atr=atr_proxy,
            squeeze_risk=squeeze,
        )
        self._last = regime
        return regime

    def _classify(self, adx: float, chop: float) -> RegimeState:
        c = self.config
        plus = self._adx.plus_di or 0.0
        minus = self._adx.minus_di or 0.0
        if adx < c.adx_range_max and chop >= c.chop_range_min:
            return RegimeState.RANGE
        if adx >= c.adx_trend_min:
            return RegimeState.TREND_UP if plus > minus else RegimeState.TREND_DOWN
        return RegimeState.TRANSITION

    def _confidence(self, state: RegimeState, adx: float, chop: float) -> float:
        c = self.config
        if state is RegimeState.RANGE:
            a = _clamp((c.adx_range_max - adx) / max(c.adx_range_max, 1e-9))
            b = _clamp((chop - c.chop_range_min) / max(100.0 - c.chop_range_min, 1e-9))
            return _clamp(0.5 * a + 0.5 * b)
        if state in (RegimeState.TREND_UP, RegimeState.TREND_DOWN):
            a = _clamp((adx - c.adx_trend_min) / 25.0)
            b = _clamp((c.chop_range_min - chop) / max(c.chop_range_min, 1e-9))
            return _clamp(0.6 * a + 0.4 * b)
        return 0.0

    def reset(self) -> None:
        self.__init__(self.config)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

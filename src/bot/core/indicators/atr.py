"""Incremental average true range (Wilder)."""

from __future__ import annotations

from collections import deque

from bot.core.models import Bar


class ATR:
    """Wilder's Average True Range over closed bars.

    ``value`` is ``None`` until ``period`` true ranges have been observed.
    The seed is the simple mean of the first ``period`` true ranges, matching
    the standard WildER definition used by most charting platforms.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self._trs: deque[float] = deque(maxlen=period)
        self._prev_close: float | None = None
        self._value: float | None = None
        self._n = 0

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, bar: Bar) -> float | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low, abs(bar.high - self._prev_close), abs(bar.low - self._prev_close))
        self._prev_close = bar.close
        self._n += 1

        if self._value is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self._value = sum(self._trs) / self.period
        else:
            self._value = (self._value * (self.period - 1) + tr) / self.period
        return self._value

    def reset(self) -> None:
        self._trs.clear()
        self._prev_close = None
        self._value = None
        self._n = 0


class RollingStd:
    """Incremental rolling standard deviation (Welford reset on eviction)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._values: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._sumsq = 0.0

    @property
    def ready(self) -> bool:
        return len(self._values) == self.period

    def update(self, x: float) -> float | None:
        if len(self._values) == self.period:
            old = self._values[0]
            self._sum -= old
            self._sumsq -= old * old
        self._values.append(x)
        self._sum += x
        self._sumsq += x * x
        if len(self._values) < self.period:
            return None
        mean = self._sum / self.period
        var = max(0.0, self._sumsq / self.period - mean * mean)
        return var**0.5


class EMA:
    """Exponential moving average seeded with an SMA."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period = period
        self._seed: list[float] = []
        self._value: float | None = None
        self.alpha = 2.0 / (period + 1.0)

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        return self._value is not None

    def update(self, x: float) -> float | None:
        if self._value is None:
            self._seed.append(x)
            if len(self._seed) == self.period:
                self._value = sum(self._seed) / self.period
            return self._value
        self._value = self.alpha * x + (1 - self.alpha) * self._value
        return self._value

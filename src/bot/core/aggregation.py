"""Incremental, look-ahead-free bar aggregation.

The canonical historical dataset is 1m OHLCV. Higher timeframes are built by
this module in both backtest and live so that a decision made at 09:30:00 UTC
sees exactly the same bars in both environments.

Rules
-----
* Buckets are aligned to UTC epoch multiples of the timeframe.
* A bucket closes when the 1m bar whose ``ts_close_ms`` equals the bucket end
  arrives (normal path). If that bar is missing, the bucket is closed as soon
  as a later bar arrives and is flagged ``complete=False``.
* Volume and quote volume are summed; OHLC is folded.
* Out-of-order or duplicate bars are rejected and counted. They never mutate a
  bucket that has already been emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.core.models import Bar


@dataclass(frozen=True, slots=True)
class AggregatedBar:
    bar: Bar
    complete: bool
    expected_bars: int
    actual_bars: int

    @property
    def ts_ms(self) -> int:
        return self.bar.ts_close_ms


class BarAggregator:
    """Aggregate base bars (1m) into one higher timeframe for a single symbol."""

    def __init__(self, symbol: str, timeframe_ms: int, base_ms: int = 60_000) -> None:
        if timeframe_ms % base_ms != 0:
            raise ValueError("timeframe must be a multiple of the base interval")
        self.symbol = symbol
        self.timeframe_ms = timeframe_ms
        self.base_ms = base_ms
        self.expected_bars = timeframe_ms // base_ms

        self._bucket_start: int | None = None
        self._o = self._h = self._l = self._c = 0.0
        self._vol = self._qvol = 0.0
        self._count = 0
        self._first_open = 0
        self._last_close = 0
        self._contiguous = True
        self._last_input_close: int | None = None

        self.gaps = 0
        self.duplicates = 0
        self.out_of_order = 0

    @property
    def bucket_start(self) -> int | None:
        return self._bucket_start

    def update(self, bar: Bar) -> AggregatedBar | None:
        """Feed one closed base bar. Returns a completed higher-timeframe bar."""
        gap_before = False
        if self._last_input_close is not None:
            if bar.ts_open_ms == self._last_input_close:
                pass
            elif bar.ts_open_ms < self._last_input_close:
                if bar.ts_open_ms < (self._bucket_start or 0):
                    self.out_of_order += 1
                else:
                    self.duplicates += 1
                return None
            else:
                self.gaps += 1
                gap_before = True
        self._last_input_close = bar.ts_close_ms

        bucket = bar.ts_open_ms - (bar.ts_open_ms % self.timeframe_ms)
        if self._bucket_start is None:
            self._start(bucket)
        elif bucket != self._bucket_start:
            emitted = self._close(force_complete=False)
            self._start(bucket)
            self._add(bar)
            if gap_before:
                self._contiguous = False
            return emitted

        self._add(bar)
        if gap_before:
            self._contiguous = False

        if bar.ts_close_ms == self._bucket_start + self.timeframe_ms:
            return self._close(force_complete=True)
        return None

    def flush(self) -> AggregatedBar | None:
        """Close the in-progress bucket (end of stream)."""
        if self._bucket_start is None:
            return None
        return self._close(force_complete=False)

    def _start(self, bucket_start: int) -> None:
        self._bucket_start = bucket_start
        self._o = self._h = self._l = self._c = 0.0
        self._vol = self._qvol = 0.0
        self._count = 0
        self._first_open = 0
        self._last_close = 0
        self._contiguous = True

    def _add(self, bar: Bar) -> None:
        if self._count == 0:
            self._o = bar.open
            self._h = bar.high
            self._l = bar.low
            self._first_open = bar.ts_open_ms
        else:
            self._h = max(self._h, bar.high)
            self._l = min(self._l, bar.low)
        self._c = bar.close
        self._vol += bar.volume
        self._qvol += bar.quote_volume
        self._last_close = bar.ts_close_ms
        self._count += 1

    def _close(self, *, force_complete: bool) -> AggregatedBar | None:
        if self._bucket_start is None or self._count == 0:
            self._bucket_start = None
            return None
        start = self._bucket_start
        end = start + self.timeframe_ms
        expected = self.expected_bars
        aligned = self._first_open == start and self._last_close == end
        complete = bool(force_complete and self._contiguous and self._count == expected and aligned)
        bar = Bar(
            symbol=self.symbol,
            ts_open_ms=start,
            ts_close_ms=end,
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
            volume=self._vol,
            quote_volume=self._qvol,
        )
        out = AggregatedBar(bar=bar, complete=complete, expected_bars=expected, actual_bars=self._count)
        self._bucket_start = None
        self._count = 0
        return out


@dataclass(slots=True)
class TimeframeSet:
    """A set of aggregators fed by the same 1m stream."""

    symbol: str
    timeframes_ms: tuple[int, ...]
    base_ms: int = 60_000
    _aggs: dict[int, BarAggregator] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._aggs = {tf: BarAggregator(self.symbol, tf, self.base_ms) for tf in self.timeframes_ms}

    def update(self, bar: Bar) -> list[AggregatedBar]:
        out: list[AggregatedBar] = []
        for tf in self.timeframes_ms:
            agg = self._aggs[tf].update(bar)
            if agg is not None:
                out.append(agg)
        return out

    def flush(self) -> list[AggregatedBar]:
        out: list[AggregatedBar] = []
        for tf in self.timeframes_ms:
            agg = self._aggs[tf].flush()
            if agg is not None:
                out.append(agg)
        return out

    @property
    def gap_count(self) -> int:
        return sum(a.gaps for a in self._aggs.values())

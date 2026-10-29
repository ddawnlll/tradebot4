"""Per-symbol state: aggregation, zones, regime and liquidity.

The state object is fed only closed 1m bars. Everything it exposes is derived
from data at or before ``last_ts_ms``. This is the single source of features
for backtest, shadow and live.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from bot.core.aggregation import AggregatedBar, TimeframeSet
from bot.core.indicators.atr import ATR
from bot.core.indicators.regime import RangeRegimeDetector, RegimeConfig
from bot.core.indicators.zones import PivotConfig, ZoneConfig, ZoneDetector
from bot.core.models import (
    HOUR_MS,
    MINUTE_MS,
    Bar,
    InstrumentSpec,
    MarketRegime,
    RegimeState,
    Zone,
)

TF_15M = 15 * MINUTE_MS
TF_1H = HOUR_MS
TF_4H = 4 * HOUR_MS


@dataclass(slots=True)
class SymbolConfig:
    entry_tf_ms: int = TF_15M
    context_tf_ms: int = TF_1H
    higher_tf_ms: int = TF_4H
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    pivot: PivotConfig = field(default_factory=PivotConfig)
    regime_context: RegimeConfig = field(default_factory=RegimeConfig)
    regime_higher: RegimeConfig = field(default_factory=RegimeConfig)


@dataclass(slots=True)
class SymbolUpdate:
    symbol: str
    ts_ms: int
    entry_bar: Bar | None = None
    entry_complete: bool = False
    context_bar: Bar | None = None
    context_complete: bool = False
    higher_bar: Bar | None = None
    higher_complete: bool = False


class SymbolState:
    def __init__(self, spec: InstrumentSpec, config: SymbolConfig | None = None) -> None:
        self.spec = spec
        self.config = config or SymbolConfig()
        self.timeframes = TimeframeSet(
            spec.symbol,
            (self.config.entry_tf_ms, self.config.context_tf_ms, self.config.higher_tf_ms),
        )
        self.zones = ZoneDetector(spec.symbol, self.config.zone, self.config.pivot)
        self.regime_context = RangeRegimeDetector(self.config.regime_context)
        self.regime_higher = RangeRegimeDetector(self.config.regime_higher)
        self.atr_entry = ATR(self.config.zone.atr_period)

        self.bars_entry: deque[Bar] = deque(maxlen=600)
        self.bars_context: deque[Bar] = deque(maxlen=400)
        self.bars_higher: deque[Bar] = deque(maxlen=200)
        self._qv: deque[tuple[int, float, float]] = deque()  # (ts, quote_volume, volume)
        self._qv_sum = 0.0
        self._vol_sum = 0.0

        self.last_1m: Bar | None = None
        self.last_entry: Bar | None = None
        self.last_context: Bar | None = None
        self.last_higher: Bar | None = None
        self.last_ts_ms: int = 0
        self.gaps = 0

    # ------------------------------------------------------------------ feeds
    def on_1m(self, bar: Bar) -> SymbolUpdate:
        if self.last_1m is not None and bar.ts_open_ms <= self.last_1m.ts_open_ms:
            # Out-of-order input is rejected; the bridge counts it separately.
            return SymbolUpdate(self.spec.symbol, self.last_ts_ms)
        if self.last_1m is not None and bar.ts_open_ms != self.last_1m.ts_close_ms:
            self.gaps += 1
        self.last_1m = bar
        self.last_ts_ms = bar.ts_close_ms

        self._qv.append((bar.ts_close_ms, bar.quote_volume, bar.volume))
        self._qv_sum += bar.quote_volume
        self._vol_sum += bar.volume
        cutoff = bar.ts_close_ms - 24 * HOUR_MS
        while self._qv and self._qv[0][0] <= cutoff:
            _, qv, vol = self._qv.popleft()
            self._qv_sum -= qv
            self._vol_sum -= vol

        update = SymbolUpdate(self.spec.symbol, bar.ts_close_ms)
        for agg in self.timeframes.update(bar):
            self._route(agg, update)
        return update

    def _route(self, agg: AggregatedBar, update: SymbolUpdate) -> None:
        tf = agg.bar.duration_ms
        if tf == self.config.entry_tf_ms:
            self.last_entry = agg.bar
            self.bars_entry.append(agg.bar)
            self.atr_entry.update(agg.bar)
            self.zones.update(agg.bar)
            update.entry_bar = agg.bar
            update.entry_complete = agg.complete
        elif tf == self.config.context_tf_ms:
            self.last_context = agg.bar
            self.bars_context.append(agg.bar)
            self.regime_context.update(agg.bar)
            update.context_bar = agg.bar
            update.context_complete = agg.complete
        elif tf == self.config.higher_tf_ms:
            self.last_higher = agg.bar
            self.bars_higher.append(agg.bar)
            self.regime_higher.update(agg.bar)
            update.higher_bar = agg.bar
            update.higher_complete = agg.complete

    def flush(self) -> None:
        self.timeframes.flush()

    # -------------------------------------------------------------- accessors
    @property
    def symbol(self) -> str:
        return self.spec.symbol

    @property
    def atr(self) -> float | None:
        return self.atr_entry.value

    @property
    def quote_volume_24h(self) -> float:
        return self._qv_sum

    @property
    def median_entry_quote_volume(self) -> float:
        if not self.bars_entry:
            return 0.0
        vals = sorted(b.quote_volume for b in self.bars_entry)
        return vals[len(vals) // 2]

    @property
    def context_regime(self) -> MarketRegime | None:
        return self.regime_context.last

    @property
    def higher_regime(self) -> MarketRegime | None:
        return self.regime_higher.last

    @property
    def ready(self) -> bool:
        return (
            self.last_entry is not None
            and self.last_context is not None
            and self.atr_entry.ready
            and self.regime_context.state is not RegimeState.UNKNOWN
        )

    def supports_below(self, price: float, min_touches: int = 2) -> list[Zone]:
        return [z for z in self.zones.zones("SUPPORT", min_touches) if z.center < price]

    def resistances_above(self, price: float, min_touches: int = 2) -> list[Zone]:
        return [z for z in self.zones.zones("RESISTANCE", min_touches) if z.center > price]

    def features(self) -> dict:
        return {
            "symbol": self.symbol,
            "last_ts_ms": self.last_ts_ms,
            "close": self.last_1m.close if self.last_1m else None,
            "atr": self.atr,
            "quote_volume_24h": self.quote_volume_24h,
            "regime": self.regime_context.state.value,
            "regime_confidence": self.regime_context.last.confidence if self.regime_context.last else 0.0,
            "higher_regime": self.regime_higher.state.value,
            "gaps": self.gaps,
            "zones": self.zones.snapshot(),
        }

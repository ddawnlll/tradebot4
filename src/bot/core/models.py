"""Core domain model.

This module is framework independent: it must never import nautilus_trader,
binance SDKs, streamlit or duckdb. Everything the trading intelligence needs to
describe a decision lives here.

Time convention
---------------
All timestamps are UTC epoch milliseconds. A 1m bar with ``ts_open_ms = T``
covers ``[T, T + 60_000)`` and its ``ts_close_ms`` is the *exclusive* end.
A bar becomes actionable only when ``ts_close_ms`` has been reached, which is
exactly the timestamp NautilusTrader receives as ``ts_event``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

MS = 1
SECOND_MS = 1_000
MINUTE_MS = 60 * SECOND_MS
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


class RegimeState(StrEnum):
    RANGE = "RANGE"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class RiskState(StrEnum):
    ACTIVE = "ACTIVE"
    REDUCING = "REDUCING"
    HALTED = "HALTED"


class ExitReason(StrEnum):
    STOP = "STOP"
    STOP_BREAKEVEN = "STOP_BREAKEVEN"
    TP1 = "TP1"
    TP2 = "TP2"
    BREAKOUT = "BREAKOUT"
    TIME_STOP = "TIME_STOP"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    RISK_HALT = "RISK_HALT"
    KILL_SWITCH = "KILL_SWITCH"
    MANUAL = "MANUAL"
    END_OF_DATA = "END_OF_DATA"


class EntryType(StrEnum):
    MARKET = "MARKET"
    LIMIT_POST_ONLY = "LIMIT_POST_ONLY"


class RejectReason(StrEnum):
    NO_RANGE = "NO_RANGE"
    REGIME = "REGIME"
    NOT_AT_EDGE = "NOT_AT_EDGE"
    NO_REJECTION = "NO_REJECTION"
    MOMENTUM = "MOMENTUM"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    BREAKOUT_RISK = "BREAKOUT_RISK"
    SQUEEZE_RISK = "SQUEEZE_RISK"
    NARROW_RANGE = "NARROW_RANGE"
    HIGH_COST = "HIGH_COST"
    LOW_SCORE = "LOW_SCORE"
    MIN_NOTIONAL = "MIN_NOTIONAL"
    MIN_QUANTITY = "MIN_QUANTITY"
    MARGIN = "MARGIN"
    LEVERAGE = "LEVERAGE"
    LIQUIDATION_BUFFER = "LIQUIDATION_BUFFER"
    RISK_POLICY = "RISK_POLICY"
    MAX_POSITIONS = "MAX_POSITIONS"
    LIQUIDITY = "LIQUIDITY"
    STALE_DATA = "STALE_DATA"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    COOLDOWN = "COOLDOWN"
    ENTRY_TIMEOUT = "ENTRY_TIMEOUT"


@dataclass(frozen=True, slots=True)
class Bar:
    """A closed OHLCV bar."""

    symbol: str
    ts_open_ms: int
    ts_close_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def __post_init__(self) -> None:
        if self.ts_close_ms <= self.ts_open_ms:
            raise ValueError("bar close must be after open")
        if not (self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close)):
            raise ValueError(f"inconsistent OHLC for {self.symbol} at {self.ts_open_ms}")

    @property
    def duration_ms(self) -> int:
        return self.ts_close_ms - self.ts_open_ms

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Normalized Binance USDT-M perpetual instrument metadata."""

    symbol: str
    base_asset: str
    quote_asset: str = "USDT"
    price_tick: float = 0.01
    qty_step: float = 0.001
    min_qty: float = 0.001
    max_qty: float = 1e9
    min_notional: float = 5.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    max_leverage: float = 10.0
    status: str = "TRADING"
    onboard_ms: int = 0

    @property
    def price_precision(self) -> int:
        return _decimals(self.price_tick)

    @property
    def qty_precision(self) -> int:
        return _decimals(self.qty_step)

    def round_price(self, price: float, side: Side | None = None) -> float:
        """Round to the tick. ``side`` biases adverse to the trader."""
        return _round_to_step(price, self.price_tick, mode="up" if side is Side.LONG else "down" if side is Side.SHORT else "nearest")

    def round_price_towards(self, price: float, direction: Literal["up", "down"]) -> float:
        return _round_to_step(price, self.price_tick, mode=direction)

    def round_qty(self, qty: float, mode: Literal["up", "down", "nearest"] = "down") -> float:
        return _round_to_step(qty, self.qty_step, mode=mode)

    def notional(self, qty: float, price: float) -> float:
        return abs(qty) * price

    def validate_order(self, qty: float, price: float, *, allow_zero: bool = False) -> str | None:
        """Return a :class:`RejectReason` value, or ``None`` when the order is valid."""
        if qty <= 0:
            return RejectReason.MIN_QUANTITY.value if not allow_zero else None
        rounded = self.round_qty(qty)
        if rounded <= 0 or rounded + 1e-15 < self.min_qty:
            return RejectReason.MIN_QUANTITY.value
        if self.notional(rounded, price) + 1e-9 < self.min_notional:
            return RejectReason.MIN_NOTIONAL.value
        return None


def _decimals(step: float) -> int:
    """Number of decimal places implied by a tick/step size."""
    if step <= 0:
        raise ValueError("step must be positive")
    text = f"{step:.12f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def _round_to_step(value: float, step: float, mode: Literal["up", "down", "nearest"]) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    q = value / step
    if mode == "up":
        n = math.ceil(q - 1e-9)
    elif mode == "down":
        n = math.floor(q + 1e-9)
    else:
        n = round(q)
    return n * step


@dataclass(frozen=True, slots=True)
class Pivot:
    kind: Literal["HIGH", "LOW"]
    price: float
    bar_ts_open_ms: int
    confirmed_ts_ms: int


@dataclass(frozen=True, slots=True)
class Zone:
    kind: Literal["SUPPORT", "RESISTANCE"]
    lower: float
    upper: float
    touches: int
    first_touch_ms: int
    last_touch_ms: int
    strength: float = 0.0

    @property
    def center(self) -> float:
        return (self.lower + self.upper) / 2.0

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass(frozen=True, slots=True)
class MarketRegime:
    state: RegimeState
    confidence: float
    duration_bars: int
    volatility: float
    breakout_risk: float
    adx: float = 0.0
    choppiness: float = 0.0
    atr: float = 0.0
    squeeze_risk: float = 0.0

    @property
    def is_range(self) -> bool:
        return self.state is RegimeState.RANGE


@dataclass(frozen=True, slots=True)
class RangeSetup:
    """A validated range with the levels a trade would use."""

    symbol: str
    support: Zone
    resistance: Zone
    mid: float
    width: float
    width_pct: float
    duration_bars: int
    atr: float
    breakout_risk: float
    squeeze_risk: float
    score: float
    score_components: dict[str, float] = field(default_factory=dict)
    ts_ms: int = 0

    @property
    def lower(self) -> float:
        return self.support.lower

    @property
    def upper(self) -> float:
        return self.resistance.upper


@dataclass(frozen=True, slots=True)
class PositionView:
    symbol: str
    side: Side
    qty: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float | None
    opened_ts_ms: int
    bars_held: int = 0
    tp1_filled: bool = False
    risk_usdt: float = 0.0
    initial_qty: float = 0.0
    signal_id: str = ""
    mae: float = 0.0
    mfe: float = 0.0


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    ts_ms: int
    equity: float
    balance: float
    available_margin: float
    used_margin: float
    unrealized_pnl: float
    open_positions: tuple[PositionView, ...] = ()
    realized_pnl_today: float = 0.0
    peak_equity: float = 0.0
    risk_state: RiskState = RiskState.ACTIVE

    def position(self, symbol: str) -> PositionView | None:
        for p in self.open_positions:
            if p.symbol == symbol:
                return p
        return None


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """The only way the core asks for an order. Adapters translate this."""

    signal_id: str
    symbol: str
    side: Side
    action: Literal["ENTRY", "EXIT", "PROTECT", "CANCEL_ENTRY"]
    entry_type: EntryType
    entry_price: float
    stop_price: float
    take_profits: tuple[float, ...]
    quantity: float
    risk_usdt: float
    strategy_version: str
    reason: str
    ts_ms: int
    reduce_only: bool = False
    post_only: bool = False
    close_reason: ExitReason | None = None
    legs: tuple[float, ...] = ()  # fraction of quantity per take profit
    leg_kinds: tuple[str, ...] = ()  # TP1 / TP2 labels for the take profits
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str | None
    quantity: float
    risk_usdt: float
    margin_required: float
    leverage: float
    liquidation_price: float | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SignalDecision:
    ts_ms: int
    symbol: str
    action: str  # ENTER / REJECT / HOLD / EXIT
    side: Side | None
    reason: str
    reject_reason: RejectReason | None = None
    score: float | None = None
    regime: MarketRegime | None = None
    setup: RangeSetup | None = None
    intent: OrderIntent | None = None
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TradeResult:
    trade_id: str
    signal_id: str
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    entry_ts_ms: int
    exit_ts_ms: int
    gross_pnl: float
    fees: float
    funding: float
    slippage: float
    net_pnl: float
    net_r: float
    risk_usdt: float
    stop_price: float
    tp1_price: float
    tp2_price: float | None
    mae_r: float
    mfe_r: float
    tp1_filled: bool
    exit_reason: ExitReason
    strategy_version: str
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def holding_ms(self) -> int:
        return self.exit_ts_ms - self.entry_ts_ms


def config_hash(payload: dict[str, Any]) -> str:
    """Stable short hash used for run provenance."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def timeframe_label(tf_ms: int) -> str:
    if tf_ms % DAY_MS == 0:
        return f"{tf_ms // DAY_MS}d"
    if tf_ms % HOUR_MS == 0:
        return f"{tf_ms // HOUR_MS}h"
    if tf_ms % MINUTE_MS == 0:
        return f"{tf_ms // MINUTE_MS}m"
    return f"{tf_ms}ms"

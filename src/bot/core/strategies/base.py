"""Strategy interface shared by backtest, shadow and live.

A strategy never submits orders. It returns an :class:`EntryProposal` or an
:class:`ExitSignal`; the decision engine applies risk and turns them into
:class:`OrderIntent` objects, which adapters translate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from bot.core.market.state import SymbolState
from bot.core.models import (
    Bar,
    EntryType,
    ExitReason,
    MarketRegime,
    PortfolioSnapshot,
    PositionView,
    RangeSetup,
    RejectReason,
    Side,
)


@dataclass(slots=True)
class EntryContext:
    state: SymbolState
    bar: Bar
    setup: RangeSetup | None
    regime: MarketRegime
    higher_regime: MarketRegime | None
    portfolio: PortfolioSnapshot
    ts_ms: int
    liquidity_score: float = 0.0


@dataclass(slots=True)
class ExitContext:
    state: SymbolState
    bar: Bar
    position: PositionView
    regime: MarketRegime | None
    setup: RangeSetup | None = None


@dataclass(slots=True)
class EntryProposal:
    side: Side
    entry_price: float
    stop_price: float
    take_profits: tuple[float, ...]
    reason: str
    entry_type: EntryType = EntryType.MARKET
    post_only: bool = False
    hold_bars: int = 0
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntryDecision:
    proposal: EntryProposal | None
    reject_reason: RejectReason | None = None
    detail: str = ""


@dataclass(slots=True)
class ExitSignal:
    reason: ExitReason
    detail: str = ""


class StrategyProtocol(Protocol):
    name: str
    version: str

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision: ...

    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None: ...

    def config_dict(self) -> dict[str, Any]: ...


class StrategyBase:
    name = "base"
    version = "0"

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:  # pragma: no cover - interface
        raise NotImplementedError

    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None:  # pragma: no cover - interface
        return None

    def config_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}

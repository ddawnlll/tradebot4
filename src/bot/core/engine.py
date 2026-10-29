"""Decision engine: the single trading brain used in every environment.

The engine consumes closed 1m bars, maintains per-symbol state, evaluates the
strategy on completed entry-timeframe bars, applies risk, and emits
:class:`OrderIntent` objects. It never imports a broker, backtester or UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from bot.core.market.scanner import MarketScanner, ScannerConfig
from bot.core.market.scoring import RangeScoreConfig
from bot.core.market.state import SymbolConfig, SymbolState, SymbolUpdate
from bot.core.models import (
    Bar,
    EntryType,
    ExitReason,
    InstrumentSpec,
    MarketRegime,
    OrderIntent,
    PortfolioSnapshot,
    PositionView,
    RangeSetup,
    RegimeState,
    RejectReason,
    RiskState,
    Side,
    SignalDecision,
    TradeResult,
)
from bot.core.risk.policy import RiskPolicy
from bot.core.risk.sizing import SizingConfig, size_position, split_legs
from bot.core.strategies.base import EntryContext, EntryProposal, ExitContext, StrategyBase


@dataclass(slots=True)
class ManagedPosition:
    signal_id: str
    symbol: str
    side: Side
    qty: float
    initial_qty: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float | None
    opened_ts_ms: int
    risk_usdt: float
    strategy_version: str
    setup: RangeSetup | None = None
    tp1_filled: bool = False
    tp2_filled: bool = False
    entry_fees: float = 0.0
    exit_fees: float = 0.0
    funding: float = 0.0
    realized: float = 0.0
    exit_notional: float = 0.0
    bars_held: int = 0
    mae_price: float = 0.0
    mfe_price: float = 0.0
    last_ts_ms: int = 0
    stop_order_id: str | None = None
    tp_order_ids: tuple[str, ...] = ()
    entry_slippage: float = 0.0
    filled_entry_qty: float = 0.0
    is_open: bool = False
    exit_pending: bool = False
    last_protect_ts: int = 0
    last_price: float = 0.0
    pending_protect: bool = False
    pending_move_stop: bool = False
    pending_since_ts: int = 0
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def notional_at_entry(self) -> float:
        return self.initial_qty * self.entry_price

    @property
    def stop_moved_to_breakeven(self) -> bool:
        if self.side is Side.LONG:
            return self.stop_price >= self.entry_price
        return self.stop_price <= self.entry_price


@dataclass(frozen=True, slots=True)
class FillEvent:
    ts_ms: int
    symbol: str
    kind: Literal["ENTRY", "TP1", "TP2", "STOP", "EXIT"]
    price: float
    qty: float
    fee: float
    order_id: str = ""
    reason: ExitReason | None = None


@dataclass(slots=True)
class EngineConfig:
    strategy_name: str = "range_v1"
    strategy_version: str = "1.0.0"
    max_positions: int = 1
    two_leg_tp: bool = True
    record_rejected_setups: bool = True
    record_all_rejects: bool = False
    range_score: RangeScoreConfig = field(default_factory=RangeScoreConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    symbol: SymbolConfig = field(default_factory=SymbolConfig)
    use_regime_filter: bool = True
    require_context: bool = True
    max_bar_volume_participation: float = 0.02
    reentry_cooldown_ms: int = 900_000


class DecisionEngine:
    def __init__(
        self,
        strategy: StrategyBase,
        specs: dict[str, InstrumentSpec],
        config: EngineConfig | None = None,
        risk_policy: RiskPolicy | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config or EngineConfig(strategy_name=strategy.name, strategy_version=strategy.version)
        self.specs = specs
        self.risk = risk_policy or RiskPolicy(_default_policy(self.config))
        self._emit = emit or (lambda kind, payload: None)
        self.states: dict[str, SymbolState] = {
            s: SymbolState(spec, self.config.symbol) for s, spec in specs.items()
        }
        self.scanner = MarketScanner(self.config.scanner, self.config.range_score)
        self.positions: dict[str, ManagedPosition] = {}
        self.closed_trades: list[TradeResult] = []
        self.last_snapshot: PortfolioSnapshot | None = None
        self.reject_counts: dict[str, int] = {}
        self.entry_bar_count = 0
        self.last_exit_ts: dict[str, int] = {}
        self.data_last_ts_ms = 0

    # ------------------------------------------------------------------ input
    def on_bar(self, bar: Bar, snapshot: PortfolioSnapshot | None = None) -> list[OrderIntent]:
        state = self.states.get(bar.symbol)
        if state is None:
            return []
        if snapshot is not None:
            self.last_snapshot = snapshot
            self.risk.on_equity(bar.ts_close_ms, snapshot.equity)
        self.data_last_ts_ms = bar.ts_close_ms

        pos = self.positions.get(bar.symbol)
        if pos is not None and pos.is_open:
            self._update_excursion(pos, bar)
            pos.last_ts_ms = bar.ts_close_ms

        update = state.on_1m(bar)
        intents: list[OrderIntent] = []

        if pos is not None and pos.is_open and pos.pending_protect and bar.ts_close_ms > pos.pending_since_ts:
            pos.pending_protect = False
            move = pos.pending_move_stop and self._breakeven_is_valid(pos, bar.close)
            intents.extend(self._protect_intent(pos, bar.ts_close_ms, move_stop=move))
            pos.pending_move_stop = False

        if pos is not None and pos.is_open and not pos.exit_pending:
            exit_intent = self._check_position_exit(pos, bar, state)
            if exit_intent is not None:
                intents.append(exit_intent)
                return intents

        if update.entry_bar is not None and update.entry_complete:
            self.entry_bar_count += 1
            intents.extend(self._on_entry_bar(state, update, bar.ts_close_ms))
        return intents

    def _on_entry_bar(self, state: SymbolState, update: SymbolUpdate, ts_ms: int) -> list[OrderIntent]:
        bar = update.entry_bar
        assert bar is not None
        regime = state.context_regime
        higher = state.higher_regime

        if self.risk.state is RiskState.HALTED:
            return self._flatten_all(ts_ms, ExitReason.KILL_SWITCH)

        pos = self.positions.get(state.symbol)
        proposal: EntryProposal | None = None
        setup: RangeSetup | None = None
        reject: RejectReason | None = None
        detail = ""

        if pos is None and self.risk.state is RiskState.ACTIVE:
            last_exit = self.last_exit_ts.get(state.symbol)
            if last_exit is not None and ts_ms - last_exit < self.config.reentry_cooldown_ms:
                self.reject_counts[RejectReason.COOLDOWN.value] = self.reject_counts.get(RejectReason.COOLDOWN.value, 0) + 1
                return []
            if regime is None and self.config.require_context:
                reject, detail = RejectReason.MISSING_CONTEXT, "no regime"
            elif self.config.require_context and not state.ready:
                reject, detail = RejectReason.MISSING_CONTEXT, "warmup"
            else:
                liquidity = self.scanner.liquidity_score(state, self.states)
                if getattr(self.strategy, "needs_range_setup", True):
                    setup = self.scanner.build_setup(state, bar, regime, liquidity)
                ctx = EntryContext(
                    state=state,
                    bar=bar,
                    setup=setup,
                    regime=regime or MarketRegime(RegimeState.UNKNOWN, 0.0, 0, 0.0, 1.0),
                    higher_regime=higher,
                    portfolio=self.last_snapshot or _empty_snapshot(ts_ms),
                    ts_ms=ts_ms,
                    liquidity_score=liquidity,
                )
                decision = self.strategy.evaluate_entry(ctx)
                proposal = decision.proposal
                reject = decision.reject_reason
                detail = decision.detail

                if proposal is not None:
                    intent, reject, detail = self._build_intent(state, proposal, setup, ts_ms)
                    if intent is not None:
                        self._record_signal(ts_ms, state, proposal.side, "ENTER", reject, setup, regime, detail, proposal)
                        return [intent]

        if self.config.record_rejected_setups and (setup is not None or (self.config.record_all_rejects and reject is not None)):
            self._record_signal(ts_ms, state, None, "REJECT", reject, setup, regime, detail, proposal)
        if reject is not None:
            self.reject_counts[reject.value] = self.reject_counts.get(reject.value, 0) + 1
        return []

    def _build_intent(
        self,
        state: SymbolState,
        proposal: EntryProposal,
        setup: RangeSetup | None,
        ts_ms: int,
    ) -> tuple[OrderIntent | None, RejectReason | None, str]:
        snapshot = self.last_snapshot or _empty_snapshot(ts_ms)
        blocked = self.risk.allow_entry(ts_ms, snapshot, data_ts_ms=self.data_last_ts_ms)
        if blocked is not None:
            return None, _as_reject(blocked), "risk policy"

        decision = size_position(
            equity=snapshot.equity,
            available_margin=snapshot.available_margin,
            entry_price=proposal.entry_price,
            stop_price=proposal.stop_price,
            spec=state.spec,
            side=proposal.side,
            config=self.config.sizing,
            risk_fraction=self.risk.risk_fraction(),
        )
        if not decision.approved:
            return None, _as_reject(decision.reason), f"sizing: {decision.reason}"

        bar_volume = state.last_entry.volume if state.last_entry is not None else 0.0
        if bar_volume > 0 and decision.quantity > self.config.max_bar_volume_participation * bar_volume:
            return (
                None,
                RejectReason.LIQUIDITY,
                f"qty {decision.quantity:.6f} > {self.config.max_bar_volume_participation:.1%} of bar volume {bar_volume:.6f}",
            )

        asset = state.spec
        side = proposal.side
        stop = asset.round_price_towards(proposal.stop_price, "down" if side is Side.LONG else "up")
        tps = [
            asset.round_price_towards(tp, "up" if side is Side.LONG else "down")
            for tp in proposal.take_profits
            if (tp > proposal.entry_price if side is Side.LONG else tp < proposal.entry_price)
        ]
        if not tps:
            return None, RejectReason.NO_RANGE, "no valid tp"

        legs: tuple[float, ...] = ()
        if self.config.two_leg_tp and len(tps) >= 2:
            split = split_legs(decision.quantity, asset, proposal.entry_price, n_legs=2)
            if split is not None:
                legs = (split[0] / decision.quantity, split[1] / decision.quantity)
            else:
                tps = tps[:1]

        signal_id = f"{self.strategy.name}-{state.symbol}-{ts_ms}"
        leg_kinds = tuple("TP1" if i == 0 else "TP2" for i in range(len(tps)))
        intent = OrderIntent(
            signal_id=signal_id,
            symbol=state.symbol,
            side=side,
            action="ENTRY",
            entry_type=EntryType.MARKET,
            entry_price=proposal.entry_price,
            stop_price=stop,
            take_profits=tuple(tps),
            quantity=decision.quantity,
            risk_usdt=decision.risk_usdt,
            strategy_version=self.strategy.version,
            reason=proposal.reason,
            ts_ms=ts_ms,
            legs=legs,
            leg_kinds=leg_kinds,
            features={
                "setup_score": setup.score if setup else None,
                "setup_components": setup.score_components if setup else {},
                "regime": setup and None,
                "stop_pct": abs(proposal.stop_price - proposal.entry_price) / proposal.entry_price,
                "sizing": decision.notes,
            },
        )
        # Register the pending position so concurrent signals cannot double-open.
        self.positions[state.symbol] = ManagedPosition(
            signal_id=signal_id,
            symbol=state.symbol,
            side=side,
            qty=decision.quantity,
            initial_qty=decision.quantity,
            entry_price=proposal.entry_price,
            stop_price=stop,
            tp1_price=tps[0],
            tp2_price=tps[1] if len(tps) > 1 else None,
            opened_ts_ms=ts_ms,
            risk_usdt=decision.risk_usdt,
            strategy_version=self.strategy.version,
            setup=setup,
            mae_price=proposal.entry_price,
            mfe_price=proposal.entry_price,
            last_ts_ms=ts_ms,
            last_price=proposal.entry_price,
            features={"legs": legs, "tp_count": len(tps)},
        )
        self.risk.on_position_opened(decision.risk_usdt)
        return intent, None, "ok"

    def _check_position_exit(self, pos: ManagedPosition, bar: Bar, state: SymbolState) -> OrderIntent | None:
        # Hard risk states always flatten.
        if self.risk.state is RiskState.HALTED:
            return self._exit_intent(pos, bar, ExitReason.KILL_SWITCH)
        if self.risk.state is RiskState.REDUCING:
            return self._exit_intent(pos, bar, ExitReason.RISK_HALT)

        view = self.position_view(pos.symbol)
        if view is not None:
            signal = self.strategy.evaluate_exit(
                ExitContext(
                    state=state,
                    bar=bar,
                    position=view,
                    regime=state.context_regime,
                    setup=pos.setup,
                )
            )
            if signal is not None:
                return self._exit_intent(pos, bar, signal.reason)

        time_stop_bars = getattr(getattr(self.strategy, "config", None), "time_stop_bars", None)
        if (
            time_stop_bars is not None
            and not pos.tp1_filled
            and pos.bars_held >= int(time_stop_bars) * (state.config.entry_tf_ms // 60_000)
        ):
            return self._exit_intent(pos, bar, ExitReason.TIME_STOP)
        return None

    def _exit_intent(self, pos: ManagedPosition, bar: Bar, reason: ExitReason) -> OrderIntent:
        pos.exit_pending = True
        return OrderIntent(
            signal_id=pos.signal_id,
            symbol=pos.symbol,
            side=pos.side.opposite,
            action="EXIT",
            entry_type=EntryType.MARKET,
            entry_price=bar.close,
            stop_price=0.0,
            take_profits=(),
            quantity=pos.qty,
            risk_usdt=pos.risk_usdt,
            strategy_version=pos.strategy_version,
            reason=reason.value,
            ts_ms=bar.ts_close_ms,
            reduce_only=True,
            close_reason=reason,
        )

    def _flatten_all(self, ts_ms: int, reason: ExitReason) -> list[OrderIntent]:
        out: list[OrderIntent] = []
        for pos in list(self.positions.values()):
            state = self.states.get(pos.symbol)
            bar = state.last_1m if state else None
            price = bar.close if bar else pos.entry_price
            out.append(
                OrderIntent(
                    signal_id=pos.signal_id,
                    symbol=pos.symbol,
                    side=pos.side.opposite,
                    action="EXIT",
                    entry_type=EntryType.MARKET,
                    entry_price=price,
                    stop_price=0.0,
                    take_profits=(),
                    quantity=pos.qty,
                    risk_usdt=pos.risk_usdt,
                    strategy_version=pos.strategy_version,
                    reason=reason.value,
                    ts_ms=ts_ms,
                    reduce_only=True,
                    close_reason=reason,
                )
            )
        return out

    # ------------------------------------------------------------------ fills
    def on_fill(self, event: FillEvent) -> list[OrderIntent]:
        pos = self.positions.get(event.symbol)
        if pos is None:
            return []
        pos.last_ts_ms = event.ts_ms
        intents: list[OrderIntent] = []

        if event.kind == "ENTRY":
            pos.entry_fees += event.fee
            pos.entry_slippage += abs(event.price - pos.entry_price) * event.qty
            pos.entry_price = _weighted_avg(pos.entry_price, pos.filled_entry_qty, event.price, event.qty)
            pos.filled_entry_qty += event.qty
            pos.qty = pos.filled_entry_qty
            pos.initial_qty = pos.filled_entry_qty
            pos.is_open = True
            if event.ts_ms == pos.last_protect_ts:
                return []
            return self._protect_intent(pos, event.ts_ms)

        if event.kind in ("TP1", "TP2", "STOP", "EXIT"):
            pos.exit_fees += event.fee
            pos.realized += _pnl(pos.side, pos.entry_price, event.price, event.qty)
            pos.exit_notional += event.price * event.qty
            pos.qty = max(0.0, pos.qty - event.qty)
            if pos.qty > 1e-12:
                # Partial fill: only re-issue protection when the whole
                # protective order family must be resized.
                if event.kind == "TP1":
                    pos.tp1_filled = True
                    pos.pending_protect = True
                    pos.pending_move_stop = True
                    pos.pending_since_ts = event.ts_ms
                    return []
                if event.kind == "EXIT":
                    pos.exit_pending = False
                    return self._protect_intent(pos, event.ts_ms)
                return []
            if event.kind == "TP1":
                pos.tp1_filled = True
                pos.pending_protect = True
                pos.pending_move_stop = True
                pos.pending_since_ts = event.ts_ms
            pos.tp2_filled = event.kind == "TP2"
            pos.exit_pending = True
            if event.reason is not None:
                reason = event.reason
            elif event.kind == "STOP":
                reason = ExitReason.STOP_BREAKEVEN if pos.stop_moved_to_breakeven else ExitReason.STOP
            elif event.kind == "TP2":
                reason = ExitReason.TP2
            elif event.kind == "TP1":
                reason = ExitReason.TP1
            else:
                reason = ExitReason.MANUAL
            self._close_position(pos, event, reason)
            return []
        return intents

    def _breakeven_is_valid(self, pos: ManagedPosition, price: float) -> bool:
        spec = self.specs[pos.symbol]
        cost = pos.entry_price * (2 * spec.taker_fee + 0.0004)
        candidate = pos.entry_price + cost if pos.side is Side.LONG else pos.entry_price - cost
        return candidate < price if pos.side is Side.LONG else candidate > price

    def _protect_intent(self, pos: ManagedPosition, ts_ms: int, *, move_stop: bool = False) -> list[OrderIntent]:
        pos.last_protect_ts = ts_ms
        stop = pos.stop_price
        if move_stop:
            # Breakeven plus round-trip costs after TP1.
            spec = self.specs[pos.symbol]
            cost = pos.entry_price * (2 * spec.taker_fee + 0.0004)
            candidate = pos.entry_price + cost if pos.side is Side.LONG else pos.entry_price - cost
            if pos.side is Side.LONG:
                candidate = max(candidate, pos.stop_price)
                valid = candidate < pos.last_price if pos.last_price else True
            else:
                candidate = min(candidate, pos.stop_price)
                valid = candidate > pos.last_price if pos.last_price else True
            if valid:
                pos.stop_price = candidate
            stop = pos.stop_price
        remaining_tps = tuple(tp for i, tp in enumerate((pos.tp1_price, pos.tp2_price)) if tp is not None and not (i == 0 and pos.tp1_filled))
        kinds = tuple(k for i, k in enumerate(("TP1", "TP2")) if not (i == 0 and pos.tp1_filled) and (pos.tp1_price if i == 0 else pos.tp2_price) is not None)
        return [
            OrderIntent(
                signal_id=pos.signal_id,
                symbol=pos.symbol,
                side=pos.side.opposite,
                action="PROTECT",
                entry_type=EntryType.MARKET,
                entry_price=pos.entry_price,
                stop_price=stop,
                take_profits=remaining_tps,
                quantity=pos.qty,
                risk_usdt=pos.risk_usdt,
                strategy_version=pos.strategy_version,
                reason="protect",
                ts_ms=ts_ms,
                reduce_only=True,
                legs=pos.features.get("legs", ()),
                leg_kinds=kinds,
            )
        ]

    def _close_position(self, pos: ManagedPosition, event: FillEvent, reason: ExitReason) -> None:
        avg_exit = pos.exit_notional / max(pos.initial_qty, 1e-12)
        gross = _pnl(pos.side, pos.entry_price, avg_exit, pos.initial_qty)
        net = gross - pos.entry_fees - pos.exit_fees + pos.funding
        risk = max(pos.risk_usdt, 1e-9)
        trade = TradeResult(
            trade_id=f"{pos.signal_id}-T",
            signal_id=pos.signal_id,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=avg_exit,
            quantity=pos.initial_qty,
            entry_ts_ms=pos.opened_ts_ms,
            exit_ts_ms=event.ts_ms,
            gross_pnl=gross,
            fees=pos.entry_fees + pos.exit_fees,
            funding=pos.funding,
            slippage=pos.entry_slippage,
            net_pnl=net,
            net_r=net / risk,
            risk_usdt=pos.risk_usdt,
            stop_price=pos.stop_price,
            tp1_price=pos.tp1_price,
            tp2_price=pos.tp2_price,
            mae_r=(pos.mae_price - pos.entry_price) * pos.side.sign * pos.initial_qty / risk,
            mfe_r=(pos.mfe_price - pos.entry_price) * pos.side.sign * pos.initial_qty / risk,
            tp1_filled=pos.tp1_filled,
            exit_reason=reason,
            strategy_version=pos.strategy_version,
            features={
                "setup_score": pos.setup.score if pos.setup else None,
                "setup_components": pos.setup.score_components if pos.setup else {},
                "bars_held": pos.bars_held,
                "funding": pos.funding,
            },
        )
        self.closed_trades.append(trade)
        self.positions.pop(pos.symbol, None)
        self.last_exit_ts[pos.symbol] = event.ts_ms
        self.risk.on_position_closed(pos.risk_usdt)
        self.risk.on_trade_closed(event.ts_ms, net)
        self._emit("trade", _trade_payload(trade))

    # ------------------------------------------------------------------ misc
    def add_funding(self, symbol: str, amount: float) -> None:
        pos = self.positions.get(symbol)
        if pos is not None:
            pos.funding += amount

    def force_close(self, symbol: str, ts_ms: int, price: float, reason: ExitReason = ExitReason.MANUAL) -> None:
        """Close a position that the broker no longer has (reconciliation)."""
        pos = self.positions.get(symbol)
        if pos is not None and pos.is_open:
            self.on_fill(FillEvent(ts_ms=ts_ms, symbol=symbol, kind="EXIT", price=price, qty=pos.qty, fee=0.0, reason=reason))
        else:
            self.on_entry_rejected(symbol)

    def on_entry_rejected(self, symbol: str) -> None:
        """Entry order never filled: release the pending slot and risk."""
        pos = self.positions.pop(symbol, None)
        if pos is not None and not pos.is_open:
            self.risk.on_position_closed(pos.risk_usdt)

    def finalize(self, ts_ms: int, prices: dict[str, float] | None = None) -> list[TradeResult]:
        """Close all open positions at the last known price (end of data)."""
        prices = prices or {}
        out: list[TradeResult] = []
        before = len(self.closed_trades)
        for symbol in list(self.positions):
            pos = self.positions.get(symbol)
            if pos is None:
                continue
            if not pos.is_open:
                # Order never filled; nothing to close.
                self.positions.pop(symbol, None)
                self.risk.on_position_closed(pos.risk_usdt)
                continue
            state = self.states.get(symbol)
            px = prices.get(symbol) or (state.last_1m.close if state and state.last_1m else pos.entry_price)
            self.on_fill(FillEvent(ts_ms=ts_ms, symbol=symbol, kind="EXIT", price=px, qty=pos.qty, fee=0.0, reason=ExitReason.END_OF_DATA))
        out = self.closed_trades[before:]
        return out

    def _update_excursion(self, pos: ManagedPosition, bar: Bar) -> None:
        pos.bars_held += 1
        pos.last_price = bar.close
        if pos.side is Side.LONG:
            pos.mae_price = min(pos.mae_price, bar.low)
            pos.mfe_price = max(pos.mfe_price, bar.high)
        else:
            pos.mae_price = max(pos.mae_price, bar.high)
            pos.mfe_price = min(pos.mfe_price, bar.low)

    def _record_signal(
        self,
        ts_ms: int,
        state: SymbolState,
        side: Side | None,
        action: str,
        reject: RejectReason | None,
        setup: RangeSetup | None,
        regime: MarketRegime | None,
        detail: str,
        proposal: EntryProposal | None,
    ) -> None:
        decision = SignalDecision(
            ts_ms=ts_ms,
            symbol=state.symbol,
            action=action,
            side=side,
            reason=detail,
            reject_reason=reject,
            score=setup.score if setup else None,
            regime=regime,
            setup=setup,
            features={
                "close": state.last_1m.close if state.last_1m else None,
                "atr": state.atr,
                "quote_volume_24h": state.quote_volume_24h,
                "components": setup.score_components if setup else {},
                "stop_pct": (
                    abs(proposal.stop_price - proposal.entry_price) / proposal.entry_price if proposal else None
                ),
            },
        )
        self._emit("signal", _signal_payload(decision))

    def position_view(self, symbol: str) -> PositionView | None:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        return PositionView(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            stop_price=pos.stop_price,
            tp1_price=pos.tp1_price,
            tp2_price=pos.tp2_price,
            opened_ts_ms=pos.opened_ts_ms,
            tp1_filled=pos.tp1_filled,
            risk_usdt=pos.risk_usdt,
            initial_qty=pos.initial_qty,
            signal_id=pos.signal_id,
        )

    def open_positions(self) -> tuple[PositionView, ...]:
        return tuple(v for s in self.positions if (v := self.position_view(s)) is not None)


def _default_policy(cfg: EngineConfig):
    from bot.core.risk.policy import RiskPolicyConfig

    return RiskPolicyConfig(max_positions=cfg.max_positions, risk_fraction=cfg.sizing.risk_fraction)


def _empty_snapshot(ts_ms: int) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        ts_ms=ts_ms, equity=0.0, balance=0.0, available_margin=0.0, unrealized_pnl=0.0, used_margin=0.0
    )


def _as_reject(reason: str | None) -> RejectReason:
    if reason is None:
        return RejectReason.RISK_POLICY
    try:
        return RejectReason(reason)
    except ValueError:
        return RejectReason.RISK_POLICY


def _pnl(side: Side, entry: float, exit_: float, qty: float) -> float:
    return (exit_ - entry) * qty * side.sign


def _weighted_avg(old_price: float, old_qty: float, new_price: float, new_qty: float) -> float:
    total = old_qty + new_qty
    if total <= 0:
        return new_price
    return (old_price * old_qty + new_price * new_qty) / total


def _trade_payload(t: TradeResult) -> dict:
    return {
        "trade_id": t.trade_id,
        "signal_id": t.signal_id,
        "symbol": t.symbol,
        "side": t.side.value,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "quantity": t.quantity,
        "entry_ts_ms": t.entry_ts_ms,
        "exit_ts_ms": t.exit_ts_ms,
        "gross_pnl": t.gross_pnl,
        "fees": t.fees,
        "funding": t.funding,
        "slippage": t.slippage,
        "net_pnl": t.net_pnl,
        "net_r": t.net_r,
        "risk_usdt": t.risk_usdt,
        "mae_r": t.mae_r,
        "mfe_r": t.mfe_r,
        "tp1_filled": t.tp1_filled,
        "exit_reason": t.exit_reason.value,
        "strategy_version": t.strategy_version,
        "features": t.features,
    }


def _signal_payload(s: SignalDecision) -> dict:
    return {
        "ts_ms": s.ts_ms,
        "symbol": s.symbol,
        "action": s.action,
        "side": s.side.value if s.side else None,
        "reason": s.reason,
        "reject_reason": s.reject_reason.value if s.reject_reason else None,
        "score": s.score,
        "regime": s.regime.state.value if s.regime else None,
        "regime_confidence": s.regime.confidence if s.regime else None,
        "support": s.setup.support.lower if s.setup else None,
        "resistance": s.setup.resistance.upper if s.setup else None,
        "features": s.features,
    }

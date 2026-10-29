"""Thin NautilusTrader v2 strategy adapter.

This file is the only place where core decisions meet the backtest engine. It
contains **no** market intelligence: no zones, no regime, no scoring, no
sizing. It translates :class:`OrderIntent` into Nautilus orders and normalizes
Nautilus events back into core fill events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import (
    Bar,
    BarType,
    ClientOrderId,
    Currency,
    InstrumentId,
    OrderFilled,
    OrderSide,
    PositionAdjustmentType,
    TimeInForce,
    Venue,
)
from nautilus_trader.trading import Strategy

from bot.backtest.instruments import nautilus_id
from bot.core.engine import DecisionEngine, FillEvent
from bot.core.models import Bar as CoreBar
from bot.core.models import OrderIntent, PortfolioSnapshot, Side

MS = 1_000_000  # ns per ms


def _clamp_intent_quantity(intent: OrderIntent, net_qty: float) -> OrderIntent:
    if intent.quantity <= net_qty + 1e-12:
        return intent
    from dataclasses import replace

    return replace(intent, quantity=net_qty, legs=())


class BridgeConfig(StrategyConfig):
    """StrategyConfig subclass; keyword-only custom fields per v2 rules."""

    def __init__(self, *, bar_types: list[str], venue: str = "BINANCE", bar_interval_ms: int = 60_000, **kwargs: Any) -> None:
        super().__init__()
        self.bar_types = bar_types
        self.venue = venue
        self.bar_interval_ms = bar_interval_ms


@dataclass(slots=True)
class BridgeOutput:
    orders: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class _OrderMeta:
    client_id: str
    symbol: str
    kind: str  # ENTRY | STOP | TP1 | TP2 | EXIT
    leg: int = 0
    requested_qty: float = 0.0
    filled_qty: float = 0.0
    reason: Any = None
    signal_id: str = ""

    @property
    def complete(self) -> bool:
        return self.filled_qty >= self.requested_qty - 1e-12


class BacktestBridge(Strategy):
    def __init__(self, config: BridgeConfig) -> None:
        super().__init__(config)
        self._cfg = config
        self._engine: DecisionEngine = config.engine  # attached by the runner
        self._instruments = {s: self._engine.states[s].spec for s in self._engine.states}
        self._nautilus_ids = {s: nautilus_id(s, config.venue) for s in self._engine.states}
        self._symbol_map = {f"{s}-PERP": s for s in self._engine.states}
        self.out = BridgeOutput()
        self._orders: dict[str, _OrderMeta] = {}
        self._protective: dict[str, dict[str, str]] = {}
        self._seq = 0
        self._usdt = Currency.from_str("USDT")
        self._last_snapshot_ts = -1
        self._snapshot_cache: PortfolioSnapshot | None = None
        self._funding_total = 0.0
        self._last_funding_capture_ts = -1
        self._engine._emit = self._record_core_event  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ start
    def on_start(self) -> None:
        for bt in self._cfg.bar_types:
            self.subscribe_bars(BarType.from_str(bt))
        self.log.info(f"bridge started for {len(self._instruments)} symbols")

    # -------------------------------------------------------------------- bar
    def on_bar(self, bar: Bar) -> None:
        core_bar = self._to_core(bar)
        ts = core_bar.ts_close_ms
        if ts % 900_000 == 0 or self._snapshot_cache is None:
            self._snapshot_cache = self._snapshot(ts)
            self.out.equity.append(
                {
                    "ts_ms": ts,
                    "equity": self._snapshot_cache.equity,
                    "balance": self._snapshot_cache.balance,
                    "used_margin": self._snapshot_cache.used_margin,
                    "unrealized_pnl": self._snapshot_cache.unrealized_pnl,
                }
            )
            if ts != self._last_funding_capture_ts:
                self._last_funding_capture_ts = ts
                self._capture_funding()
        intents = self._engine.on_bar(core_bar, self._snapshot_cache)
        for intent in intents:
            self._execute(intent)

    # ---------------------------------------------------------------- execute
    def _execute(self, intent: OrderIntent) -> None:
        try:
            if intent.action == "ENTRY":
                self._submit_entry(intent)
            elif intent.action == "PROTECT":
                self._submit_protection(intent)
            elif intent.action == "EXIT":
                self._submit_exit(intent)
        except Exception as exc:  # pragma: no cover - defensive
            self._error(intent.ts_ms, intent.symbol, "execute_failed", str(exc))
            raise

    def _submit_entry(self, intent: OrderIntent) -> None:
        inst = self._instrument(intent.symbol)
        cid = self._next_id("E")
        side = OrderSide.BUY if intent.side is Side.LONG else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=inst.id,
            order_side=side,
            quantity=inst.make_qty(intent.quantity, round_down=True),
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            client_order_id=ClientOrderId(cid),
        )
        self._orders[cid] = _OrderMeta(cid, intent.symbol, "ENTRY", requested_qty=intent.quantity, signal_id=intent.signal_id)
        self._record_order(intent.ts_ms, intent.symbol, cid, "ENTRY", "MARKET", None, intent.quantity)
        self.submit_order(order)

    def _submit_protection(self, intent: OrderIntent) -> None:
        inst = self._instrument(intent.symbol)
        state = self._engine.states.get(intent.symbol)
        last_px = state.last_1m.close if state and state.last_1m else None
        # The broker position is authoritative. If a fill landed while we were
        # replacing orders, the intent size can exceed the live position and a
        # reduce-only TP would be rejected.
        net = self.portfolio.net_position(inst.id)
        if net is not None:
            net_qty = abs(float(net))
            if net_qty <= 1e-12:
                px = last_px if last_px is not None else self._engine.positions[intent.symbol].entry_price
                self._engine.force_close(intent.symbol, intent.ts_ms, px)
                return
            intent = _clamp_intent_quantity(intent, net_qty)
        long_position = intent.side is Side.SHORT  # closing side SELL means long
        if last_px is not None:
            invalid = (long_position and intent.stop_price >= last_px) or (
                not long_position and intent.stop_price <= last_px
            )
            if invalid:
                # Never cancel a valid older stop for an unplaceable replacement.
                self._engine.positions[intent.symbol].pending_protect = False
                return
        self._cancel_protective(intent.symbol)
        try:
            qty = inst.make_qty(intent.quantity, round_down=True)
        except ValueError:
            qty = None
        if qty is None or qty.as_double() <= 0:
            self._close_or_reject(intent, inst)
            return
        stop_cid = self._next_id("S")
        stop = self.order_factory.stop_market(
            instrument_id=inst.id,
            order_side=OrderSide.SELL if intent.side is Side.SHORT else OrderSide.BUY,
            quantity=qty,
            trigger_price=inst.make_price(intent.stop_price),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=ClientOrderId(stop_cid),
        )
        self._orders[stop_cid] = _OrderMeta(stop_cid, intent.symbol, "STOP", requested_qty=qty.as_double(), signal_id=intent.signal_id)
        self._protective.setdefault(intent.symbol, {})["STOP"] = stop_cid
        self._record_order(intent.ts_ms, intent.symbol, stop_cid, "STOP_MARKET", "STOP", intent.stop_price, qty.as_double())
        self.submit_order(stop)

        tps = intent.take_profits
        legs = intent.legs
        for i, tp in enumerate(tps):
            try:
                if len(tps) == 1:
                    leg_qty = qty
                elif legs and len(legs) == len(tps):
                    leg_qty = inst.make_qty(intent.quantity * legs[i], round_down=True)
                else:
                    leg_qty = inst.make_qty(intent.quantity / len(tps), round_down=True)
            except ValueError:
                # Remaining size is below one step; the stop covers it.
                continue
            if leg_qty.as_double() <= 0:
                continue
            cid = self._next_id("P")
            order = self.order_factory.limit(
                instrument_id=inst.id,
                order_side=OrderSide.SELL if intent.side is Side.SHORT else OrderSide.BUY,
                quantity=leg_qty,
                price=inst.make_price(tp),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                client_order_id=ClientOrderId(cid),
            )
            kind = intent.leg_kinds[i] if i < len(intent.leg_kinds) else ("TP1" if i == 0 else "TP2")
            self._orders[cid] = _OrderMeta(cid, intent.symbol, kind, leg=i, requested_qty=leg_qty.as_double(), signal_id=intent.signal_id)
            self._protective.setdefault(intent.symbol, {})[kind] = cid
            self._record_order(intent.ts_ms, intent.symbol, cid, "LIMIT", kind, tp, leg_qty.as_double())
            self.submit_order(order)
    def _submit_exit(self, intent: OrderIntent) -> None:
        inst = self._instrument(intent.symbol)
        self._cancel_protective(intent.symbol)
        try:
            qty = inst.make_qty(intent.quantity, round_down=True)
        except ValueError:
            qty = None
        if qty is None or qty.as_double() <= 0:
            self._close_or_reject(intent, inst)
            return
        cid = self._next_id("X")
        order = self.order_factory.market(
            instrument_id=inst.id,
            order_side=OrderSide.SELL if intent.side is Side.SHORT else OrderSide.BUY,
            quantity=qty,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=ClientOrderId(cid),
        )
        self._orders[cid] = _OrderMeta(cid, intent.symbol, "EXIT", requested_qty=qty.as_double(), reason=intent.close_reason, signal_id=intent.signal_id)
        self._record_order(intent.ts_ms, intent.symbol, cid, "MARKET", "EXIT", intent.entry_price, qty.as_double())
        self.submit_order(order)

    def _close_or_reject(self, intent: OrderIntent, inst) -> None:
        """Handle a quantity that rounds to zero without losing an open position."""
        pos = self._engine.positions.get(intent.symbol)
        if pos is not None and pos.is_open:
            state = self._engine.states.get(intent.symbol)
            px = state.last_1m.close if state and state.last_1m else pos.entry_price
            self._engine.force_close(intent.symbol, intent.ts_ms, px)
        else:
            self._engine.on_entry_rejected(intent.symbol)

    def _cancel_protective(self, symbol: str) -> None:
        ids = self._protective.pop(symbol, {})
        for cid in ids.values():
            self._cancel(cid)

    def _cancel(self, cid: str) -> None:
        order = self.cache.order(ClientOrderId(cid))
        if order is not None:
            import contextlib

            with contextlib.suppress(Exception):  # already closed
                self.cancel_order(order)

    # ----------------------------------------------------------------- events
    def on_order_filled(self, event: OrderFilled) -> None:
        cid = event.client_order_id.value
        meta = self._orders.get(cid)
        symbol = self._core_symbol(event.instrument_id)
        ts_ms = event.ts_event // MS
        px = event.last_px.as_double()
        qty = event.last_qty.as_double()
        fee = event.commission.as_double() if event.commission is not None else 0.0
        kind = meta.kind if meta else "UNKNOWN"
        if meta is not None:
            meta.filled_qty += qty
            if meta.complete:
                self._orders.pop(cid, None)
        self.out.fills.append(
            {
                "ts_ms": ts_ms,
                "symbol": symbol,
                "client_order_id": cid,
                "kind": kind,
                "side": "BUY" if event.order_side is OrderSide.BUY else "SELL",
                "price": px,
                "qty": qty,
                "fee": fee,
                "liquidity": "TAKER" if event.liquidity_side.name == "TAKER" else "MAKER",
                "order_type": event.order_type.name,
                "signal_id": meta.signal_id if meta else "",
            }
        )
        if meta is None:
            return
        if meta is not None and kind in ("TP1", "TP2") and meta.complete:
            prot = self._protective.get(symbol, {})
            prot.pop(kind, None)
        reason = meta.reason if meta is not None else None
        if kind == "STOP":
            from bot.core.models import ExitReason

            pos = self._engine.positions.get(symbol)
            reason = ExitReason.STOP_BREAKEVEN if pos is not None and pos.stop_moved_to_breakeven else ExitReason.STOP
        fill = FillEvent(ts_ms=ts_ms, symbol=symbol, kind=kind, price=px, qty=qty, fee=fee, order_id=cid, reason=reason)
        followups = self._engine.on_fill(fill)
        if symbol not in self._engine.positions:
            self._cancel_protective(symbol)
        for intent in followups:
            self._execute(intent)

    def on_order_denied(self, event) -> None:
        self._on_reject(event, "DENIED")

    def on_order_rejected(self, event) -> None:
        self._on_reject(event, "REJECTED")

    def _on_reject(self, event, status: str) -> None:
        cid = event.client_order_id.value
        meta = self._orders.pop(cid, None)
        symbol = self._core_symbol(event.instrument_id)
        ts_ms = event.ts_event // MS
        reason = getattr(event, "reason", "")
        pos_dbg = self._engine.positions.get(symbol)
        net_dbg = self.portfolio.net_position(event.instrument_id)
        self._error(
            ts_ms,
            symbol,
            f"order_{status.lower()}",
            f"{reason} | kind={meta.kind if meta else '?'} req={meta.requested_qty if meta else '?'}"
            f" pos={pos_dbg.qty if pos_dbg else None} net={net_dbg}",
        )
        if meta is None:
            return
        if meta.kind == "ENTRY":
            self._engine.on_entry_rejected(symbol)
        elif meta.kind == "STOP":
            net = self.portfolio.net_position(event.instrument_id)
            if net is not None and float(net) == 0.0:
                # Position already flat: the stop lost a race with a closing
                # fill. Reconcile the core state, do not flatten again.
                state = self._engine.states.get(symbol)
                px = state.last_1m.close if state and state.last_1m else 0.0
                self._engine.force_close(symbol, ts_ms, px)
                return
            # No stop coverage is a critical condition: flatten immediately.
            self._error(ts_ms, symbol, "missing_stop", "protective stop rejected")
            pos = self._engine.positions.get(symbol)
            if pos is not None and pos.is_open:
                from bot.core.models import EntryType, ExitReason, OrderIntent

                self._execute(
                    OrderIntent(
                        signal_id=pos.signal_id,
                        symbol=symbol,
                        side=pos.side.opposite,
                        action="EXIT",
                        entry_type=EntryType.MARKET,
                        entry_price=self._engine.states[symbol].last_1m.close if self._engine.states[symbol].last_1m else pos.entry_price,
                        stop_price=0.0,
                        take_profits=(),
                        quantity=pos.qty,
                        risk_usdt=pos.risk_usdt,
                        strategy_version=pos.strategy_version,
                        reason=ExitReason.RISK_HALT.value,
                        ts_ms=ts_ms,
                        reduce_only=True,
                        close_reason=ExitReason.RISK_HALT,
                    )
                )

    def on_position_event(self, event) -> None:
        if getattr(event, "adjustment_type", None) != PositionAdjustmentType.FUNDING:
            return
        pnl = getattr(event, "pnl_change", None)
        if pnl is None:
            return
        amount = pnl.as_double()
        symbol = self._core_symbol(event.instrument_id)
        self._engine.add_funding(symbol, amount)
        self._funding_total += amount
        self.out.funding.append({"ts_ms": event.ts_event // MS, "symbol": symbol, "amount": amount})

    def collect_funding_adjustments(self) -> list[dict]:
        """Read funding adjustments from the cache.

        Nautilus v2.0.0rc8 deliberately drops ``PositionAdjusted`` before the
        Python strategy callback (see docs/nautilus_gaps.md), so the cache is
        the only reliable source. This is called after the run and is exact:
        it uses the engine's own settlement amounts.
        """
        seen: set[str] = set()
        out: list[dict] = []
        positions = list(self.cache.positions())
        positions += list(self.cache.positions_closed())
        for pos in positions:
            pid = str(pos.id)
            if pid in seen:
                continue
            seen.add(pid)
            for adj in pos.adjustments():
                if adj.adjustment_type != PositionAdjustmentType.FUNDING:
                    continue
                pnl = adj.pnl_change
                if pnl is None:
                    continue
                out.append(
                    {
                        "symbol": self._core_symbol(adj.instrument_id),
                        "ts_ms": adj.ts_event // MS,
                        "amount": pnl.as_double(),
                        "position_id": pid,
                    }
                )
        out.sort(key=lambda x: x["ts_ms"])
        return out

    def _capture_funding(self) -> None:
        """Accumulate funding adjustments while positions are still in the cache."""
        self.out.funding = self.collect_funding_adjustments()

    # ------------------------------------------------------------------ state
    def _to_core(self, bar: Bar) -> CoreBar:
        symbol = self._core_symbol(bar.bar_type.instrument_id)
        close_ms = bar.ts_event // MS
        return CoreBar(
            symbol=symbol,
            ts_open_ms=close_ms - self._cfg.bar_interval_ms,
            ts_close_ms=close_ms,
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=bar.volume.as_double(),
            quote_volume=bar.volume.as_double() * bar.close.as_double(),
        )

    def _core_symbol(self, instrument_id: InstrumentId) -> str:
        raw = instrument_id.symbol.value
        return self._symbol_map.get(raw, raw)

    def _instrument(self, symbol: str):
        return self.cache.instrument(InstrumentId.from_str(self._nautilus_ids[symbol]))

    def _snapshot(self, ts_ms: int) -> PortfolioSnapshot:
        venue = Venue(self._cfg.venue)
        account = self.portfolio.account(venue)
        equity = 0.0
        balance = 0.0
        free = 0.0
        locked = 0.0
        upnl = 0.0
        if account is not None:
            eq = self.portfolio.equity(venue) or {}
            money = eq.get(self._usdt)
            equity = money.as_double() if money is not None else 0.0
            bt = account.balance_total(self._usdt)
            balance = bt.as_double() if bt is not None else 0.0
            bf = account.balance_free(self._usdt)
            free = bf.as_double() if bf is not None else 0.0
            bl = account.balance_locked(self._usdt)
            locked = bl.as_double() if bl is not None else 0.0
        for nid in self._nautilus_ids.values():
            u = self.portfolio.unrealized_pnl(InstrumentId.from_str(nid))
            if u is not None:
                upnl += u.as_double()
        return PortfolioSnapshot(
            ts_ms=ts_ms,
            equity=equity,
            balance=balance,
            available_margin=free,
            used_margin=locked,
            unrealized_pnl=upnl,
            open_positions=self._engine.open_positions(),
            peak_equity=self._engine.risk.peak_equity,
            risk_state=self._engine.risk.state,
        )

    # --------------------------------------------------------------- plumbing
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:011d}"

    def _record_order(self, ts_ms: int, symbol: str, cid: str, order_type: str, kind: str, price: float | None, qty: float) -> None:
        self.out.orders.append(
            {
                "ts_ms": ts_ms,
                "symbol": symbol,
                "client_order_id": cid,
                "kind": kind,
                "order_type": order_type,
                "price": price,
                "qty": qty,
            }
        )

    def _record_core_event(self, kind: str, payload: dict) -> None:
        if kind == "signal":
            self.out.signals.append(payload)

    def _error(self, ts_ms: int, symbol: str, kind: str, detail: str) -> None:
        self.out.errors.append({"ts_ms": ts_ms, "symbol": symbol, "kind": kind, "detail": detail})
        self.log.error(f"{kind} {symbol}: {detail}")

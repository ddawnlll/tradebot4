"""Fixtures for Nautilus-backed integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.config import LoggerConfig, RiskEngineConfig
from nautilus_trader.execution import DefaultFillModel, MakerTakerFeeModel
from nautilus_trader.model import AccountType, Currency, Money, OmsType, TraderId, Venue

from bot.backtest.bridge import BacktestBridge, BridgeConfig
from bot.backtest.instruments import make_instrument, nautilus_id
from bot.core.engine import DecisionEngine, EngineConfig
from bot.core.models import InstrumentSpec
from bot.core.strategies.base import (
    EntryContext,
    EntryDecision,
    EntryProposal,
    ExitContext,
    ExitSignal,
    StrategyBase,
)

VENUE = "BINANCE"
MS = 1_000_000


def synthetic_spec(symbol: str = "TESTUSDT", tick: float = 0.01, step: float = 0.001, min_notional: float = 5.0) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        price_tick=tick,
        qty_step=step,
        min_qty=step,
        min_notional=min_notional,
        maker_fee=0.0002,
        taker_fee=0.0005,
        max_leverage=10.0,
    )


class ScriptedStrategy(StrategyBase):
    """Enters once at a chosen 15m boundary, then does nothing."""

    name = "scripted"
    version = "test"
    needs_range_setup = False

    def __init__(self, enter_ts_ms: int, side, stop: float, tps: tuple[float, ...], qty_hint: float = 1.0) -> None:
        self.enter_ts_ms = enter_ts_ms
        self.side = side
        self.stop = stop
        self.tps = tps
        self.qty_hint = qty_hint

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        if ctx.ts_ms != self.enter_ts_ms:
            return EntryDecision(None)
        return EntryDecision(
            EntryProposal(
                side=self.side,
                entry_price=ctx.bar.close,
                stop_price=self.stop,
                take_profits=self.tps,
                reason="scripted",
            )
        )

    def evaluate_exit(self, ctx: ExitContext) -> ExitSignal | None:
        return None


def make_engine(strategy: StrategyBase, spec: InstrumentSpec, *, two_leg: bool = True, risk_fraction: float = 0.05) -> DecisionEngine:
    cfg = EngineConfig(
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        two_leg_tp=two_leg,
        require_context=False,
    )
    cfg.sizing = cfg.sizing.__class__(risk_fraction=risk_fraction)
    return DecisionEngine(strategy, {spec.symbol: spec}, cfg)


def run_synthetic(
    engine: DecisionEngine,
    spec: InstrumentSpec,
    prices: list[float],
    *,
    start: datetime | None = None,
    starting_balance: float = 1000.0,
    adaptive: bool = False,
    bar_volume: float = 1_000_000.0,
) -> tuple[BacktestEngine, BacktestBridge]:
    from nautilus_trader.model import Bar, BarType

    start = start or datetime(2025, 1, 1, tzinfo=UTC)
    t0 = int(start.timestamp() * 1000)
    inst = make_instrument(spec, VENUE)
    bar_type = f"{nautilus_id(spec.symbol, VENUE)}-1-MINUTE-LAST-EXTERNAL"
    bt = BarType.from_str(bar_type)

    ech = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str("BT-TEST"),
            bypass_logging=True,
            logging=LoggerConfig(stdout_level=LogLevel.ERROR),
            risk_engine=RiskEngineConfig(bypass=False),
        )
    )
    ech.add_venue(
        venue=Venue(VENUE),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=Currency.from_str("USDT"),
        starting_balances=[Money(starting_balance, Currency.from_str("USDT"))],
        default_leverage=Decimal("10"),
        bar_execution=True,
        bar_adaptive_high_low_ordering=adaptive,
        fee_model=MakerTakerFeeModel(maker_rate=Decimal("0.0002"), taker_rate=Decimal("0.0005")),
        fill_model=DefaultFillModel(prob_fill_on_limit=1.0, prob_slippage=0.0, random_seed=1),
        use_reduce_only=True,
        support_contingent_orders=False,
    )
    ech.add_instrument(inst)

    bars = []
    for i, px in enumerate(prices):
        ns = (t0 + (i + 1) * 60_000) * MS
        high = px + 0.05
        low = px - 0.05
        bars.append(
            Bar(
                bt,
                inst.make_price(px),
                inst.make_price(high),
                inst.make_price(low),
                inst.make_price(px),
                inst.make_qty(bar_volume),
                ns,
                ns,
            )
        )
    ech.add_data(bars)

    cfg = BridgeConfig(bar_types=[bar_type], venue=VENUE)
    cfg.engine = engine
    bridge = BacktestBridge(config=cfg)
    ech.add_strategy(bridge)
    ech.run()
    return ech, bridge


def flat_then_move(start_px: float, move: list[float], flat_bars: int = 20) -> list[float]:
    return [start_px] * flat_bars + move

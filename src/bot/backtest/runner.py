"""NautilusTrader v2 backtest runner.

Nautilus is the execution simulator only. The decision engine is created once
and kept alive across streaming chunks so position and indicator state are
continuous, exactly like a live process that never restarts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.config import LoggerConfig, RiskEngineConfig
from nautilus_trader.execution import DefaultFillModel, MakerTakerFeeModel, StaticLatencyModel
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    Currency,
    Money,
    OmsType,
    TraderId,
    Venue,
)

from bot.backtest.bridge import BacktestBridge, BridgeConfig
from bot.backtest.funding import funding_for_trades, load_funding
from bot.backtest.instruments import make_instrument, nautilus_id
from bot.core.engine import DecisionEngine, EngineConfig
from bot.core.market.scanner import ScannerConfig
from bot.core.market.scoring import RangeScoreConfig
from bot.core.models import InstrumentSpec, config_hash
from bot.core.risk.policy import RiskPolicy, RiskPolicyConfig
from bot.core.risk.sizing import SizingConfig
from bot.core.strategies.registry import build_strategy
from bot.data.instruments import load_instruments
from bot.data.loader import calendar_chunks, load_bars_frame
from bot.data.storage import Paths

log = logging.getLogger(__name__)
MS = 1_000_000


def _asdict(obj):
    from dataclasses import asdict

    return asdict(obj)


@dataclass(slots=True)
class BacktestConfig:
    strategy: str = "range_v1"
    strategy_params: dict = field(default_factory=dict)
    symbols: list[str] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0
    starting_balance: float = 20.0
    leverage: float = 10.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    prob_fill_on_limit: float = 0.9
    prob_slippage: float = 1.0
    random_seed: int | None = 42
    latency_ms: int = 0
    max_positions: int = 1
    two_leg_tp: bool = True
    risk_fraction: float = 0.05
    daily_loss_limit_frac: float = 0.15
    kill_switch_drawdown_frac: float = 0.50
    chunk_days: int = 15
    bar_interval_ms: int = 60_000
    adaptive_high_low: bool = False
    venue: str = "BINANCE"
    use_funding: bool = True
    strict_errors: bool = True
    journal: bool = True
    enrich: bool = True
    range_score: RangeScoreConfig = field(default_factory=RangeScoreConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "strategy_params": self.strategy_params,
            "symbols": self.symbols,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "starting_balance": self.starting_balance,
            "leverage": self.leverage,
            "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee,
            "prob_fill_on_limit": self.prob_fill_on_limit,
            "prob_slippage": self.prob_slippage,
            "random_seed": self.random_seed,
            "latency_ms": self.latency_ms,
            "max_positions": self.max_positions,
            "two_leg_tp": self.two_leg_tp,
            "risk_fraction": self.risk_fraction,
            "daily_loss_limit_frac": self.daily_loss_limit_frac,
            "kill_switch_drawdown_frac": self.kill_switch_drawdown_frac,
            "bar_interval_ms": self.bar_interval_ms,
            "adaptive_high_low": self.adaptive_high_low,
            "range_score": _asdict(self.range_score),
            "scanner": _asdict(self.scanner),
        }


@dataclass(slots=True)
class BacktestResult:
    run_id: str
    run_dir: Path
    trades: pl.DataFrame
    fills: pl.DataFrame
    orders: pl.DataFrame
    equity: pl.DataFrame
    signals: pl.DataFrame
    metrics: dict
    elapsed_s: float
    errors: list[dict]


def run_backtest(cfg: BacktestConfig, paths: Paths | None = None, *, out_dir: Path | None = None) -> BacktestResult:
    paths = paths or Paths()
    paths.ensure()
    t0 = time.perf_counter()
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{cfg.strategy}_{config_hash(cfg.as_dict())}"
    run_dir = out_dir or (paths.runs / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    specs_all = load_instruments(paths)
    if not cfg.symbols:
        raise ValueError("no symbols")
    specs = {s: _with_fees(specs_all[s], cfg) for s in cfg.symbols if s in specs_all}
    missing = [s for s in cfg.symbols if s not in specs_all]
    for s in missing:
        # Delisted symbols are absent from exchangeInfo; synthesize conservative specs from data.
        specs[s] = _synthetic_spec(s, cfg)
    if not specs:
        raise ValueError("no instrument specs")

    strategy = build_strategy(cfg.strategy, cfg.strategy_params)
    engine_cfg = EngineConfig(
        strategy_name=cfg.strategy,
        strategy_version=strategy.version,
        max_positions=cfg.max_positions,
        two_leg_tp=cfg.two_leg_tp,
        range_score=cfg.range_score,
        scanner=cfg.scanner,
        sizing=SizingConfig(risk_fraction=cfg.risk_fraction, max_leverage=cfg.leverage),
    )
    risk = RiskPolicy(
        RiskPolicyConfig(
            max_positions=cfg.max_positions,
            risk_fraction=cfg.risk_fraction,
            daily_loss_limit_frac=cfg.daily_loss_limit_frac,
            kill_switch_drawdown_frac=cfg.kill_switch_drawdown_frac,
        )
    )
    decision = DecisionEngine(strategy, specs, engine_cfg, risk)

    venue_name = cfg.venue
    usdt = Currency.from_str("USDT")
    bt_engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId.from_str("BT-001"),
            risk_engine=RiskEngineConfig(bypass=False),
            bypass_logging=True,
            logging=LoggerConfig(stdout_level=LogLevel.INFO if os.environ.get("TRADEBOT_BT_LOGS", "0") == "1" else LogLevel.ERROR),
        )
    )
    bt_engine.add_venue(
        venue=Venue(venue_name),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=usdt,
        starting_balances=[Money(cfg.starting_balance, usdt)],
        default_leverage=Decimal(str(cfg.leverage)),
        bar_execution=True,
        bar_adaptive_high_low_ordering=cfg.adaptive_high_low,
        fee_model=MakerTakerFeeModel(maker_rate=Decimal(str(cfg.maker_fee)), taker_rate=Decimal(str(cfg.taker_fee))),
        fill_model=DefaultFillModel(
            prob_fill_on_limit=cfg.prob_fill_on_limit,
            prob_slippage=cfg.prob_slippage,
            random_seed=cfg.random_seed,
        ),
        latency_model=StaticLatencyModel(base_latency_nanos=cfg.latency_ms * 1_000_000) if cfg.latency_ms else None,
        liquidation_enabled=True,
        use_reduce_only=True,
        support_contingent_orders=False,
    )

    instruments = {}
    for s, spec in specs.items():
        inst = make_instrument(spec, venue_name)
        instruments[s] = inst
        bt_engine.add_instrument(inst)

    bar_types = {s: f"{nautilus_id(s, venue_name)}-1-MINUTE-LAST-EXTERNAL" for s in specs}
    bridge_config = BridgeConfig(
        bar_types=[bar_types[s] for s in specs],
        venue=venue_name,
        bar_interval_ms=cfg.bar_interval_ms,
    )
    bridge_config.engine = decision
    bridge = BacktestBridge(config=bridge_config)
    bt_engine.add_strategy(bridge)

    funding_path = paths.funding / "funding.parquet"
    chunks = calendar_chunks(cfg.start_ms, cfg.end_ms, cfg.chunk_days)
    total_bars = 0
    for ci, (c_start, c_end) in enumerate(chunks, start=1):
        for s in specs:
            df = load_bars_frame(paths, s, c_start, c_end)
            if df.height == 0:
                continue
            total_bars += df.height
            bars = _to_nautilus_bars(df, instruments[s], bar_types[s])
            bt_engine.add_data(bars)
            if cfg.use_funding:
                fu = load_funding(funding_path, s, nautilus_id(s, venue_name), c_start - 86_400_000, c_end)
                if fu:
                    bt_engine.add_data(fu)
        bt_engine.run(streaming=True)
        bt_engine.clear_data()
        log.info("chunk %d/%d done: %d bars total", ci, len(chunks), total_bars)
    bt_engine.end()

    last_prices = {s: st.last_1m.close for s, st in decision.states.items() if st.last_1m is not None}
    decision.finalize(cfg.end_ms, last_prices)

    fills_pre = _frame(bridge.out.fills)
    funding_df = pl.read_parquet(paths.funding / "funding.parquet") if (paths.funding / "funding.parquet").exists() else pl.DataFrame()
    funding_events: list[dict] = []
    if funding_df.height and cfg.use_funding:
        funding_events, decision.closed_trades = funding_for_trades(
            decision.closed_trades, fills_pre, funding_df, paths
        )
    bridge.out.funding = funding_events
    cache_funding = bridge.collect_funding_adjustments()

    if cfg.strict_errors and bridge.out.errors:
        first = bridge.out.errors[:3]
        bt_engine.dispose()
        raise RuntimeError(f"backtest produced {len(bridge.out.errors)} bridge errors: {first}")

    elapsed = time.perf_counter() - t0

    trades = _frame([_trade_row(t) for t in decision.closed_trades])
    fills = _frame(bridge.out.fills)
    orders = _frame(bridge.out.orders)
    equity = _frame(bridge.out.equity)
    signals = _frame(bridge.out.signals)

    _dump(bt_engine, run_dir, venue_name)
    for name, df in (("trades", trades), ("fills", fills), ("orders", orders), ("equity", equity), ("signals", signals)):
        if df.height:
            df = _flatten_structs(df)
            df.write_parquet(run_dir / f"{name}.parquet", compression="zstd")

    from bot.journal.tagging import tag_trades

    tags = tag_trades(trades)
    if tags.height:
        tags.write_parquet(run_dir / "tags.parquet", compression="zstd")

    meta = {
        "run_id": run_id,
        "created": datetime.now(UTC).isoformat(),
        "nautilus_version": _nautilus_version(),
        "python": __import__("sys").version,
        "config": cfg.as_dict(),
        "config_hash": config_hash(cfg.as_dict()),
        "bars_processed": total_bars,
        "elapsed_s": elapsed,
        "bars_per_s": total_bars / elapsed if elapsed else None,
        "errors": bridge.out.errors[:200],
        "reject_counts": decision.reject_counts,
        "funding_total": sum(f["amount"] for f in funding_events),
        "funding_events": len(funding_events),
        "funding_analytic": sum(f["amount"] for f in funding_events),
        "funding_cache_total": sum(f["amount"] for f in cache_funding),
        "open_positions_at_end": len(decision.positions),
        "trades": trades.height,
        "strategy_version": strategy.version,
        "designation": "research",
        "entry_bars": decision.entry_bar_count,
        "positions_left": len(decision.positions),
    }
    (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    (run_dir / "config.yaml").write_text(_to_yaml(cfg.as_dict()))

    bt_engine.dispose()

    from bot.backtest.report import compute_metrics

    metrics = compute_metrics(trades, equity, cfg.starting_balance)
    residual = metrics["final_equity"] - cfg.starting_balance - float(trades["net_pnl"].sum()) if trades.height else metrics["final_equity"] - cfg.starting_balance
    metrics["equity_residual_usdt"] = residual
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    if abs(residual) > max(0.01, 0.005 * cfg.starting_balance):
        log.warning("account/trade reconciliation residual %.4f USDT", residual)

    if cfg.journal:
        _write_journal(run_dir, run_id, meta, signals, trades, fills, orders, equity, funding_events, tags)
    if cfg.enrich:
        from bot.journal.enrich import missed_opportunities, post_exit_drift

        try:
            drift = post_exit_drift(trades, paths)
            if drift.height:
                drift.write_parquet(run_dir / "post_exit_drift.parquet", compression="zstd")
        except Exception as exc:  # pragma: no cover - enrichment is best effort
            log.warning("post-exit drift failed: %s", exc)
        try:
            missed = missed_opportunities(signals, paths)
            if missed.height:
                missed.write_parquet(run_dir / "missed.parquet", compression="zstd")
        except Exception as exc:  # pragma: no cover
            log.warning("missed opportunity analysis failed: %s", exc)
        try:
            from bot.journal.enrich import stop_tp_ambiguity

            ambiguity = stop_tp_ambiguity(trades, paths)
            if ambiguity.height:
                ambiguity.write_parquet(run_dir / "ambiguity.parquet", compression="zstd")
                meta["ambiguous_trades"] = int((ambiguity["ambiguous_bars"] > 0).sum())
                meta["ambiguous_bars"] = int(ambiguity["ambiguous_bars"].sum())
                (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
        except Exception as exc:  # pragma: no cover
            log.warning("ambiguity audit failed: %s", exc)

    return BacktestResult(
        run_id=run_id,
        run_dir=run_dir,
        trades=trades,
        fills=fills,
        orders=orders,
        equity=equity,
        signals=signals,
        metrics=metrics,
        elapsed_s=elapsed,
        errors=bridge.out.errors,
    )


def _to_nautilus_bars(df: pl.DataFrame, instrument, bar_type: str) -> list[Bar]:
    bt = BarType.from_str(bar_type)
    opens = df["open"].to_list()
    highs = df["high"].to_list()
    lows = df["low"].to_list()
    closes = df["close"].to_list()
    vols = df["volume"].to_list()
    tss = df["ts_close_ms"].to_list()
    out: list[Bar] = []
    mk_price = instrument.make_price
    mk_qty = instrument.make_qty
    for o, h, low, c, v, ts in zip(opens, highs, lows, closes, vols, tss, strict=True):
        ns = ts * MS
        out.append(Bar(bt, mk_price(o), mk_price(h), mk_price(low), mk_price(c), mk_qty(v), ns, ns))
    return out


def _with_fees(spec: InstrumentSpec, cfg: BacktestConfig) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=spec.symbol,
        base_asset=spec.base_asset,
        quote_asset=spec.quote_asset,
        price_tick=spec.price_tick,
        qty_step=spec.qty_step,
        min_qty=spec.min_qty,
        max_qty=spec.max_qty,
        min_notional=spec.min_notional,
        maker_fee=cfg.maker_fee,
        taker_fee=cfg.taker_fee,
        max_leverage=cfg.leverage,
        status=spec.status,
        onboard_ms=spec.onboard_ms,
    )


def _synthetic_spec(symbol: str, cfg: BacktestConfig) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        price_tick=0.0001,
        qty_step=1.0,
        min_qty=1.0,
        max_qty=1e12,
        min_notional=5.0,
        maker_fee=cfg.maker_fee,
        taker_fee=cfg.taker_fee,
        max_leverage=cfg.leverage,
        status="DELISTED",
    )


def _trade_row(t) -> dict:
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
        "stop_price": t.stop_price,
        "tp1_price": t.tp1_price,
        "tp2_price": t.tp2_price,
        "mae_r": t.mae_r,
        "mfe_r": t.mfe_r,
        "tp1_filled": t.tp1_filled,
        "exit_reason": t.exit_reason.value,
        "strategy_version": t.strategy_version,
        "setup_score": t.features.get("setup_score"),
        "bars_held": t.features.get("bars_held"),
    }


def _write_journal(run_dir, run_id, meta, signals, trades, fills, orders, equity, funding, tags) -> None:
    from bot.journal.recorder import Journal

    with Journal(run_dir / "journal.sqlite", source="backtest", run_id=run_id) as j:
        j.record_run(
            meta,
            strategy=meta["config"]["strategy"],
            strategy_version=meta.get("strategy_version", meta["config"]["strategy"]),
            config_hash=meta["config_hash"],
            designation=meta.get("designation", "research"),
        )
        for _name, df, event_type, ts_col in (
            ("signals", signals, "SIGNAL", "ts_ms"),
            ("trades", trades, "TRADE", "exit_ts_ms"),
            ("fills", fills, "FILL", "ts_ms"),
            ("orders", orders, "ORDER", "ts_ms"),
            ("equity", equity, "EQUITY", "ts_ms"),
        ):
            if df.height == 0:
                continue
            for row in df.iter_rows(named=True):
                payload = _jsonable(row)
                j.record(
                    event_type,
                    ts_event_ms=int(row.get(ts_col) or 0),
                    payload=payload,
                    symbol=row.get("symbol"),
                    signal_id=row.get("signal_id"),
                    strategy_version=row.get("strategy_version"),
                    config_hash=meta["config_hash"],
                )
        for f in funding:
            j.record("FUNDING", ts_event_ms=int(f["ts_ms"]), payload=_jsonable(f), symbol=f["symbol"])
        for row in tags.iter_rows(named=True) if tags.height else []:
            j.add_tag(row["trade_id"], row["tag"], rule_version=row.get("rule_version", ""), notes=row.get("notes", ""))


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _with_funding(trade, events: list[dict]):
    from dataclasses import replace

    amount = sum(
        e["amount"]
        for e in events
        if e["symbol"] == trade.symbol and trade.entry_ts_ms <= e["ts_ms"] <= trade.exit_ts_ms
    )
    if amount == 0.0:
        return trade
    net = trade.net_pnl + amount
    return replace(
        trade,
        funding=trade.funding + amount,
        net_pnl=net,
        net_r=net / max(trade.risk_usdt, 1e-9),
    )


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def _flatten_structs(df: pl.DataFrame) -> pl.DataFrame:
    """Parquet cannot store empty structs; serialize dict columns to JSON."""
    for col, dtype in df.schema.items():
        if isinstance(dtype, pl.Struct) or dtype == pl.Object:
            df = df.with_columns(
                pl.col(col)
                .map_elements(lambda v: json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v), return_dtype=pl.Utf8)
                .alias(col)
            )
    return df


def _dump(engine: BacktestEngine, run_dir: Path, venue: str) -> None:
    reports = {
        "account": lambda: engine.generate_account_report(Venue(venue)),
        "positions": engine.generate_positions_report,
        "orders_raw": engine.generate_orders_report,
    }
    for name, fn in reports.items():
        try:
            df = fn()
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].astype(str)
            df.to_parquet(run_dir / f"{name}.parquet")
        except Exception as exc:  # pragma: no cover
            log.warning("%s report failed: %s", name, exc)


def _nautilus_version() -> str:
    import nautilus_trader

    return nautilus_trader.__version__


def _to_yaml(payload: dict) -> str:
    import yaml

    return yaml.safe_dump(payload, sort_keys=True)


def run_backtest_node(
    cfg: BacktestConfig,
    paths: Paths | None = None,
    *,
    catalog_path: Path | None = None,
    chunk_size: int = 200_000,
) -> dict:
    """Catalog-driven run through `BacktestNode` (the high-level v2 API).

    Kept deliberately small: it proves the same bridge and decision engine
    produce the same result when data comes from a Nautilus catalog. Funding
    is not part of the catalog in the pinned release, so this path is used for
    golden regression tests, not for performance claims.
    """
    from nautilus_trader.backtest import (
        BacktestDataConfig,
        BacktestNode,
        BacktestRunConfig,
        BacktestVenueConfig,
    )
    from nautilus_trader.common import LogLevel
    from nautilus_trader.config import LoggerConfig
    from nautilus_trader.model import (
        BarAggregation,
        BarSpecification,
        BookType,
        InstrumentId,
        NautilusDataType,
        PriceType,
    )
    from nautilus_trader.persistence import DataCatalogConfig

    paths = paths or Paths()
    catalog_path = catalog_path or paths.catalog
    specs_all = load_instruments(paths)
    specs = {s: _with_fees(specs_all[s], cfg) for s in cfg.symbols if s in specs_all}
    strategy = build_strategy(cfg.strategy, cfg.strategy_params)
    decision = DecisionEngine(
        strategy,
        specs,
        EngineConfig(
            strategy_name=cfg.strategy,
            strategy_version=strategy.version,
            max_positions=cfg.max_positions,
            two_leg_tp=cfg.two_leg_tp,
            range_score=cfg.range_score,
            scanner=cfg.scanner,
            sizing=SizingConfig(risk_fraction=cfg.risk_fraction, max_leverage=cfg.leverage),
        ),
        RiskPolicy(RiskPolicyConfig(max_positions=cfg.max_positions, risk_fraction=cfg.risk_fraction)),
    )
    venue = BacktestVenueConfig(
        name=cfg.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type=BookType.L1_MBP,
        starting_balances=[f"{cfg.starting_balance:g} USDT"],
        default_leverage=Decimal(str(cfg.leverage)),
        bar_execution=True,
        bar_adaptive_high_low_ordering=cfg.adaptive_high_low,
        fee_model=MakerTakerFeeModel(maker_rate=Decimal(str(cfg.maker_fee)), taker_rate=Decimal(str(cfg.taker_fee))),
        fill_model=DefaultFillModel(
            prob_fill_on_limit=cfg.prob_fill_on_limit, prob_slippage=cfg.prob_slippage, random_seed=cfg.random_seed
        ),
        use_reduce_only=True,
        support_contingent_orders=False,
    )
    spec = BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)
    data = [
        BacktestDataConfig(
            data_type=NautilusDataType.Bar,
            catalog=DataCatalogConfig(path=str(catalog_path)),
            instrument_id=InstrumentId.from_str(nautilus_id(s, cfg.venue)),
            bar_spec=spec,
            start_time=cfg.start_ms * MS,
            end_time=cfg.end_ms * MS,
        )
        for s in specs
    ]
    run_config = BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=BacktestEngineConfig(
            trader_id=TraderId.from_str("BT-NODE"),
            bypass_logging=True,
            logging=LoggerConfig(stdout_level=LogLevel.ERROR),
        ),
        start=cfg.start_ms * MS,
        end=cfg.end_ms * MS,
        chunk_size=chunk_size,
        raise_exception=True,
    )
    node = BacktestNode([run_config])
    try:
        node.build()
        bridge_config = BridgeConfig(
            bar_types=[f"{nautilus_id(s, cfg.venue)}-1-MINUTE-LAST-EXTERNAL" for s in specs],
            venue=cfg.venue,
            bar_interval_ms=cfg.bar_interval_ms,
        )
        bridge_config.engine = decision
        bridge = BacktestBridge(config=bridge_config)
        node.add_strategy(run_config.id, bridge)
        results = node.run()
    finally:
        node.dispose()
    last_prices = {s: st.last_1m.close for s, st in decision.states.items() if st.last_1m is not None}
    decision.finalize(cfg.end_ms, last_prices)
    from bot.backtest.report import compute_metrics

    trades = _frame([_trade_row(t) for t in decision.closed_trades])
    metrics = compute_metrics(trades, _frame(bridge.out.equity), cfg.starting_balance)
    return {
        "trades": trades,
        "metrics": metrics,
        "signals": _frame(bridge.out.signals),
        "fills": _frame(bridge.out.fills),
        "node_results": results,
        "errors": bridge.out.errors,
    }

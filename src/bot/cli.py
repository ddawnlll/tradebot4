"""Command line interface.

Live trading commands are intentionally absent until the validation gate
passes. `risk pause` and `rollback` are guarded: they refuse to act on a live
account because no live adapter exists yet.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bot.data.storage import Paths

app = typer.Typer(help="Binance USDT-M range trading research system", no_args_is_help=True)
data_app = typer.Typer(help="Data acquisition and normalization")
journal_app = typer.Typer(help="Journal ETL and analysis")
risk_app = typer.Typer(help="Risk controls (live only)")
app.add_typer(data_app, name="data")
app.add_typer(journal_app, name="journal")
app.add_typer(risk_app, name="risk")
console = Console()


def _ms(d: date | None, default: date) -> int:
    d = d or default
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@data_app.command("instruments")
def data_instruments() -> None:
    """Fetch Binance USDT-M instrument metadata."""
    from bot.data.instruments import fetch_exchange_info, parse_instruments, save_instruments

    specs = parse_instruments(fetch_exchange_info())
    path = save_instruments(specs)
    console.print(f"[green]saved {len(specs)} instruments to {path}[/green]")


@data_app.command("universe")
def data_universe(
    as_of: str = typer.Option(..., help="YYYY-MM-DD, point-in-time date"),
    top_n: int = typer.Option(40, help="universe size"),
    min_age_days: int = typer.Option(30),
) -> None:
    """Build a point-in-time universe from all historical symbols."""
    from bot.data.universe import build_universe

    d = date.fromisoformat(as_of)
    df = build_universe(d, top_n=top_n, min_age_days=min_age_days)
    console.print(df.filter("in_universe").head(top_n))


@data_app.command("sync")
def data_sync(
    symbols: str = typer.Option(..., help="comma separated symbols, or 'universe'"),
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(..., help="YYYY-MM-DD"),
    interval: str = typer.Option("1m"),
    funding: bool = typer.Option(True),
) -> None:
    """Download Binance public data (idempotent, checksum verified)."""
    from bot.data.download import Downloader
    from bot.data.storage import Paths as P

    paths = P()
    syms = _resolve_symbols(symbols, paths)
    stats = Downloader(paths=paths).sync(syms, date.fromisoformat(start), date.fromisoformat(end), interval=interval, funding=funding)
    console.print(json.dumps(stats, indent=2))


@data_app.command("normalize")
def data_normalize() -> None:
    """Normalize raw zips into canonical parquet."""
    from bot.data.normalize import normalize_bars, normalize_funding, raw_files_for

    paths = Paths()
    console.print(normalize_bars(raw_files_for(paths, "klines", "1m"), paths.bars_1m))
    console.print(normalize_funding(raw_files_for(paths, "fundingRate"), paths.funding))


@data_app.command("catalog")
def data_catalog(
    symbols: str = typer.Option("universe", help="comma separated or 'universe'"),
    start: str = typer.Option(...),
    end: str = typer.Option(...),
    verify: bool = typer.Option(True),
) -> None:
    """Export canonical bars into the Nautilus ParquetDataCatalog."""
    from bot.data.instruments import load_instruments
    from bot.data.to_nautilus import export_symbol, verify_symbol

    paths = Paths()
    specs = load_instruments(paths)
    syms = _resolve_symbols(symbols, paths)
    start_ms, end_ms = _ms(date.fromisoformat(start), date.today()), _ms(date.fromisoformat(end), date.today())
    rows = []
    for s in syms:
        if s not in specs:
            continue
        export_symbol(paths, specs[s], start_ms, end_ms)
        if verify:
            rows.append(verify_symbol(paths, specs[s], start_ms, end_ms))
    if rows:
        _print_rows(rows)


@data_app.command("quality")
def data_quality(
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(..., help="YYYY-MM-DD"),
    symbols: str = typer.Option("universe"),
) -> None:
    """Run data quality checks and write reports/data_quality.md."""
    from bot.data.quality import run_quality

    paths = Paths()
    payload = run_quality(paths, _resolve_symbols(symbols, paths), _ms(date.fromisoformat(start), date.today()), _ms(date.fromisoformat(end), date.today()))
    console.print(f"[green]checked {len(payload['symbols'])} symbols; report at reports/data_quality.md[/green]")


@app.command("backtest")
def backtest(
    strategy: str = typer.Option("range_v1"),
    symbols: str = typer.Option("universe"),
    start: str = typer.Option(...),
    end: str = typer.Option(...),
    balance: float = typer.Option(20.0),
    risk: float = typer.Option(0.05),
    two_leg: bool = typer.Option(True),
    chunk_days: int = typer.Option(15),
) -> None:
    """Run a NautilusTrader v2 backtest."""
    from bot.backtest.runner import BacktestConfig, run_backtest

    _setup_logging()
    paths = Paths()
    cfg = BacktestConfig(
        strategy=strategy,
        symbols=_resolve_symbols(symbols, paths),
        start_ms=_ms(date.fromisoformat(start), date.today()),
        end_ms=_ms(date.fromisoformat(end), date.today()),
        starting_balance=balance,
        risk_fraction=risk,
        two_leg_tp=two_leg,
        chunk_days=chunk_days,
    )
    res = run_backtest(cfg)
    console.print(f"[green]run {res.run_id} -> {res.run_dir}[/green]")
    _print_metrics(res.metrics)


@app.command("validate")
def validate(
    strategy: str = typer.Option("range_v1"),
    symbols: str = typer.Option("universe"),
    start: str = typer.Option("2024-11-01"),
    dev_end: str = typer.Option("2025-12-31"),
    oos_end: str = typer.Option("2026-06-30"),
    vault_end: str = typer.Option("2026-10-20"),
    workers: int = typer.Option(4),
) -> None:
    """Run walk-forward, sensitivity, benchmarks, ablations and OOS once."""
    from bot.backtest.runner import BacktestConfig
    from bot.backtest.validate import ValidationConfig, run_validation

    _setup_logging()
    paths = Paths()
    base = BacktestConfig(
        strategy=strategy,
        symbols=_resolve_symbols(symbols, paths),
        start_ms=_ms(date.fromisoformat(start), date.today()),
        end_ms=_ms(date.fromisoformat(oos_end), date.today()),
    )
    vcfg = ValidationConfig(
        base=base,
        dev_end_ms=_ms(date.fromisoformat(dev_end), date.today()),
        oos_end_ms=_ms(date.fromisoformat(oos_end), date.today()),
        vault_end_ms=_ms(date.fromisoformat(vault_end), date.today()),
        max_workers=workers,
    )
    report = run_validation(vcfg, paths)
    console.print(json.dumps(report["go_no_go"], indent=2))


@app.command("scan")
def scan(
    symbols: str = typer.Option("universe"),
    days: int = typer.Option(30),
    replay: bool = typer.Option(True),
) -> None:
    """Replay recent data through the scanner and print current range candidates."""
    from bot.core.market.scanner import MarketScanner
    from bot.core.market.state import SymbolConfig, SymbolState
    from bot.core.models import Bar
    from bot.data.instruments import load_instruments
    from bot.data.loader import load_bars_frame

    paths = Paths()
    specs = load_instruments(paths)
    syms = _resolve_symbols(symbols, paths)
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000
    scanner = MarketScanner()
    states: dict[str, SymbolState] = {}
    for s in syms:
        if s not in specs:
            continue
        df = load_bars_frame(paths, s, start_ms, end_ms)
        if df.height == 0:
            continue
        st = SymbolState(specs[s], SymbolConfig())
        states[s] = st
        for row in df.iter_rows(named=True):
            st.on_1m(
                Bar(
                    symbol=s,
                    ts_open_ms=row["ts_open_ms"],
                    ts_close_ms=row["ts_close_ms"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    quote_volume=row["volume"] * row["close"],
                )
            )
    rows = []
    for s, st in states.items():
        regime = st.context_regime
        if regime is None or not st.ready or st.last_entry is None:
            continue
        setup = scanner.build_setup(st, st.last_entry, regime, scanner.liquidity_score(st, states))
        rows.append(
            {
                "symbol": s,
                "regime": regime.state.value,
                "score": round(setup.score, 3) if setup else None,
                "support": round(setup.support.lower, 6) if setup else None,
                "resistance": round(setup.resistance.upper, 6) if setup else None,
                "close": st.last_1m.close if st.last_1m else None,
                "touches": f"{setup.support.touches}/{setup.resistance.touches}" if setup else None,
            }
        )
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    _print_rows(rows)


@journal_app.command("etl")
def journal_etl() -> None:
    """Consolidate run journals into parquet and DuckDB views."""
    from bot.journal.etl import export_all

    console.print(export_all())


@journal_app.command("enrich")
def journal_enrich(run: str | None = typer.Option(None, help="run id or path")) -> None:
    """Tag trades and compute enrichment for a run."""
    from bot.journal.enrich import missed_opportunities, post_exit_drift
    from bot.journal.tagging import tag_trades

    paths = Paths()
    run_dir = Path(run) if run else sorted(paths.runs.glob("*"))[-1]
    trades = __import__("polars").read_parquet(run_dir / "trades.parquet")
    tags = tag_trades(trades)
    tags.write_parquet(run_dir / "tags.parquet")
    drift = post_exit_drift(trades, paths)
    drift.write_parquet(run_dir / "post_exit_drift.parquet")
    signals_path = run_dir / "signals.parquet"
    if not signals_path.exists() and (run_dir / "signals").is_dir():
        signals_path = run_dir / "signals"
    import polars as pl

    signals = pl.read_parquet(sorted(signals_path.rglob("*.parquet"))) if signals_path.is_dir() else pl.read_parquet(signals_path)
    missed = missed_opportunities(signals, paths)
    if missed.height:
        missed.write_parquet(run_dir / "missed.parquet")
    console.print(f"[green]{tags.height} tags, {drift.height} drift rows, {missed.height} missed signals[/green]")


@app.command("dashboard")
def dashboard() -> None:
    """Launch the Streamlit dashboard."""
    import subprocess
    import sys

    subprocess.run([sys.executable, "-m", "streamlit", "run", "dashboard/app.py"], check=False)


@app.command("status")
def status() -> None:
    """Show data and run status."""
    paths = Paths()
    runs = sorted(p for p in paths.runs.iterdir() if (p / "metadata.json").exists())
    table = Table("item", "value")
    table.add_row("runs", str(len(runs)))
    if runs:
        table.add_row("latest run", runs[-1].name)
    table.add_row("bars parquet", str(len(list(paths.bars_1m.rglob('*.parquet')))))
    table.add_row("journal db", str((paths.journal / "journal.duckdb").exists()))
    table.add_row("live adapter", "not implemented (validation gate)")
    console.print(table)


@risk_app.command("pause")
def risk_pause(reason: str = typer.Option("manual")) -> None:
    """Pause live entries. Refuses when no live adapter/config is present."""
    raise typer.Exit(_guard_live("pause"))


@risk_app.command("resume")
def risk_resume() -> None:
    """Resume live entries. Requires human approval and a live adapter."""
    raise typer.Exit(_guard_live("resume"))


@app.command("rollback")
def rollback(to: str = typer.Option(..., help="strategy tag, e.g. strategy-v1.2.0")) -> None:
    """Roll back to an immutable strategy tag. Refuses without a live deployment."""
    raise typer.Exit(_guard_live(f"rollback to {to}"))


def _guard_live(action: str) -> int:
    console.print(f"[red]refusing to {action}: no live deployment exists for this repository. "
                  "Live execution is gated behind a GO validation result and human approval.[/red]")
    return 2


def _resolve_symbols(value: str, paths: Paths) -> list[str]:
    if value == "universe":
        files = sorted(paths.universe.glob("universe_*.parquet"))
        if not files:
            raise typer.BadParameter("no universe file; run `bot data universe` first")
        import polars as pl

        return pl.read_parquet(files[-1]).filter(pl.col("in_universe")).sort("rank")["symbol"].to_list()
    return [s.strip() for s in value.split(",") if s.strip()]


def _print_rows(rows: list[dict]) -> None:
    if not rows:
        console.print("[yellow]no rows[/yellow]")
        return
    keys = list(rows[0].keys())
    table = Table(*keys)
    for r in rows:
        table.add_row(*[str(r.get(k)) for k in keys])
    console.print(table)


def _print_metrics(m: dict) -> None:
    keys = ["trades", "net_pnl", "return_pct", "expectancy_r", "profit_factor", "win_rate", "max_drawdown_pct", "fees", "funding", "sharpe"]
    table = Table("metric", "value")
    for k in keys:
        v = m.get(k)
        table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print(table)


if __name__ == "__main__":
    app()

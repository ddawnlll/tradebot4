"""Funding rate data -> Nautilus v2 ``FundingRateUpdate`` events.

The pinned Nautilus v2 catalog has no funding-rate writer/query (see
docs/nautilus_gaps.md), so funding is fed directly with ``engine.add_data``.
The exchange settles at ``next_funding_ns``; a positive rate debits longs.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from nautilus_trader.model import FundingRateUpdate, InstrumentId

MS_TO_NS = 1_000_000
DAY_MS = 86_400_000


def load_funding(
    path: Path,
    symbol: str,
    instrument_id: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRateUpdate]:
    if not path.exists():
        return []
    df = pl.read_parquet(path).filter(
        (pl.col("symbol") == symbol) & (pl.col("ts_ms") >= start_ms) & (pl.col("ts_ms") <= end_ms)
    ).sort("ts_ms")
    ts = df["ts_ms"].to_list()
    rates = df["rate"].to_list()
    intervals = df["interval_hours"].to_list()
    out: list[FundingRateUpdate] = []
    for t, r, h in zip(ts, rates, intervals, strict=True):
        # Binance publishes the rate at the settlement instant; the position is
        # charged that rate at that instant. Scheduling the settlement 1 ms
        # later avoids the next update replacing the pending settlement timer
        # (which silently skipped almost every payment when we scheduled at the
        # *next* record's timestamp).
        settle_ns = t * MS_TO_NS
        out.append(
            FundingRateUpdate(
                instrument_id=InstrumentId.from_str(instrument_id),
                rate=_dec(r),
                ts_event=settle_ns - MS_TO_NS,
                ts_init=settle_ns - MS_TO_NS,
                interval=int(h) * 3600,
                next_funding_ns=settle_ns + MS_TO_NS,
            )
        )
    return out


def _dec(x: float):
    from decimal import Decimal

    return Decimal(f"{x:.12f}")


def validate_funding_coverage(
    paths,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
    *,
    max_gap_ms: int = DAY_MS,
) -> None:
    """Fail loudly when a traded symbol has missing or gappy funding data.

    A silent zero funding assumption is a real source of overstated PnL for a
    20 USDT account, so a missing symbol/period aborts the run instead of
    producing a quietly optimistic result.
    """
    from bot.data.loader import load_bars_frame

    path = paths.funding / "funding.parquet"
    if not path.exists():
        raise RuntimeError(f"funding data missing: {path}")
    fund = pl.read_parquet(path)
    problems: list[str] = []
    for symbol in symbols:
        bars = load_bars_frame(paths, symbol, start_ms, end_ms).filter(pl.col("volume") > 0)
        if bars.height == 0:
            # Delisted symbols keep zero-volume flat rows in the archive; they
            # cannot be traded and do not accrue funding.
            continue
        first_bar = int(bars["ts_open_ms"][0])
        last_bar = int(bars["ts_close_ms"][-1])
        sub = fund.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("ts_ms") >= first_bar)
            & (pl.col("ts_ms") <= last_bar)
        ).sort("ts_ms")
        if sub.height == 0:
            problems.append(f"{symbol}: no funding rows")
            continue
        first_fund = int(sub["ts_ms"][0])
        last_fund = int(sub["ts_ms"][-1])
        diffs = sub["ts_ms"].diff().drop_nulls()
        largest_gap = int(diffs.max()) if diffs.len() else 0
        if first_fund - first_bar > max_gap_ms:
            problems.append(f"{symbol}: first funding {first_fund} after first bar {first_bar}")
        if last_bar - last_fund > max_gap_ms:
            problems.append(f"{symbol}: last funding {last_fund} before last bar {last_bar}")
        if largest_gap > max_gap_ms:
            problems.append(f"{symbol}: funding gap {largest_gap} ms")
    if problems:
        raise RuntimeError("funding coverage incomplete: " + "; ".join(problems[:12]))


def funding_for_trades(trades, fills, funding_df, paths, tolerance: float = 1e-9) -> tuple[list[dict], list]:
    """Compute per-trade funding analytically from the position timeline.

    Nautilus v2.0.0rc8 settles funding in the simulated account but drops the
    ``PositionAdjusted`` event before the Python strategy callback, and the
    cache does not retain adjustments for closed positions. The account is
    still charged, so trading-level records must account for it explicitly.

    Position quantity at each settlement is reconstructed from the actual
    fills for that signal (entry fills minus partial exits). The price used is
    the 1m close at the settlement minute rather than the mark price; the
    difference is second-order for 1m bars and is reported in
    docs/backtest_assumptions.md.
    """
    from dataclasses import replace

    import polars as pl

    from bot.data.loader import load_bars_frame

    events: list[dict] = []
    priced: dict[tuple[str, int], float] = {}

    def close_at(symbol: str, ts_ms: int) -> float | None:
        key = (symbol, ts_ms)
        if key in priced:
            return priced[key]
        df = load_bars_frame(paths, symbol, ts_ms - 60_000, ts_ms)
        if df.height == 0:
            return None
        px = float(df["close"][-1])
        priced[key] = px
        return px

    out = []
    for trade in trades:
        sub = fills.filter((pl.col("signal_id") == trade.signal_id) & (pl.col("ts_ms") <= trade.exit_ts_ms)).sort("ts_ms")
        if sub.height == 0:
            out.append(trade)
            continue
        ts = sub["ts_ms"].to_list()
        kinds = sub["kind"].to_list()
        qtys = sub["qty"].to_list()

        def qty_at(t: int, ts=ts, kinds=kinds, qtys=qtys) -> float:
            q = 0.0
            for i, when in enumerate(ts):
                if when > t:
                    break
                if kinds[i] == "ENTRY":
                    q += qtys[i]
                elif kinds[i] in ("TP1", "TP2", "STOP", "EXIT"):
                    q -= qtys[i]
            return max(q, 0.0)

        rates = funding_df.filter(
            (pl.col("symbol") == trade.symbol)
            & (pl.col("ts_ms") > trade.entry_ts_ms)
            & (pl.col("ts_ms") <= trade.exit_ts_ms)
        )
        amount = 0.0
        for row in rates.iter_rows(named=True):
            q = qty_at(int(row["ts_ms"]))
            if q <= 1e-12:
                continue
            px = close_at(trade.symbol, int(row["ts_ms"]))
            if px is None:
                continue
            pay = -trade.side.sign * q * px * float(row["rate"])
            amount += pay
            events.append(
                {
                    "symbol": trade.symbol,
                    "ts_ms": int(row["ts_ms"]),
                    "amount": pay,
                    "signal_id": trade.signal_id,
                    "rate": float(row["rate"]),
                    "qty": q,
                    "price": px,
                }
            )
        if abs(amount) > tolerance:
            net = trade.net_pnl + amount
            out.append(replace(trade, funding=trade.funding + amount, net_pnl=net, net_r=net / max(trade.risk_usdt, 1e-9)))
        else:
            out.append(trade)
    return events, out

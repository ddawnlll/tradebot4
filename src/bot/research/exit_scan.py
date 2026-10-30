"""Conditional, offline exit-policy diagnostic.

The scan replays alternative exits on the *same* baseline entries and
quantities. It cannot change future entries, portfolio equity, kill-switch
timing or position overlap, so its output is diagnostic only. A candidate must
still go through a full Nautilus replay before any economic claim.

Rules:
* only data at or after the entry fill is used for exits (pre-entry bars are
  used only for a documented structural-trail warmup);
* a stop is checked before a target in the same bar (stop-first), gaps fill at
  the bar open, and the ambiguous-bar count is reported;
* a trailing stop is advanced only after the bar closes;
* fees, explicit slippage, funding and maker/taker liquidity are applied;
* no synthetic prices are invented.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from bot.data.loader import load_bars_frame
from bot.data.storage import Paths

MS_8H = 8 * 3_600_000
MS_24H = 24 * 3_600_000
MS_48H = 48 * 3_600_000
MS_7D = 7 * 24 * 3_600_000
TAKER = 0.0005
MAKER = 0.0002
MARKET_SLIP_BPS = 2.0
STOP_SLIP_BPS = 5.0

ARMS = [
    "baseline_actual",
    "no_breakout",
    "stop_only",
    "static_1r",
    "static_2r",
    "static_3r",
    "no_tp",
    "chandelier_2.5",
    "chandelier_3.0",
    "be_0.5",
    "be_1.0",
    "structural_24h",
    "structural_48h",
    "breakout_0.0",
    "breakout_0.6",
]
NOT_RUN = {
    "ema4h_trail": "EMA4H trail is not implemented in this scan; v8 uses an EMA-passed structural stop, not a 1m EMA.",
    "hybrid_trail": "The v8 hybrid arm stacks BE + chandelier thresholds; it is deferred until the simpler arms are interpreted.",
}


@dataclass(slots=True)
class TradeCase:
    trade_id: str
    signal_id: str
    symbol: str
    side: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    stop_price: float  # initial protective stop, not the post-BE final stop
    final_stop_price: float
    tp1_price: float
    tp2_price: float | None
    quantity: float
    tp1_qty: float
    rest_qty: float
    risk_usdt: float
    actual_net_r: float
    actual_exit_reason: str
    entry_fee: float
    support: float | None
    resistance: float | None
    atr: float | None
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_long(self) -> bool:
        return self.side.upper() == "LONG"

    @property
    def sign(self) -> float:
        return 1.0 if self.is_long else -1.0

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.stop_price)


@dataclass(slots=True)
class Leg:
    price: float
    qty: float
    reason: str
    liquidity: str  # MAKER / TAKER


@dataclass(slots=True)
class ExitResult:
    arm: str
    trades: int = 1
    net_pnl: float = 0.0
    net_r: float = 0.0
    exit_reason: str = ""
    exit_ts_ms: int = 0
    bars_held: int = 0
    ambiguous: bool = False
    funding_pnl: float = 0.0
    fees: float = 0.0
    slippage_pnl: float = 0.0
    legs: list[Leg] = field(default_factory=list)
    status: str = "OK"
    note: str = ""
    trade_id: str = ""


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signal_info(signals: pl.DataFrame, signal_id: str) -> tuple[float | None, float | None, float | None]:
    parts = signal_id.split("-")
    if len(parts) < 3:
        return None, None, None
    symbol, ts = parts[-2], int(parts[-1])
    row = signals.filter((pl.col("symbol") == symbol) & (pl.col("ts_ms") == ts) & (pl.col("action") == "ENTER"))
    if row.height == 0:
        return None, None, None
    r = row.row(0, named=True)
    feats = r.get("features")
    if isinstance(feats, str):
        try:
            feats = json.loads(feats)
        except json.JSONDecodeError:
            feats = {}
    atr = feats.get("atr") if isinstance(feats, dict) else None
    return r.get("support"), r.get("resistance"), atr


def load_cases(run_dir: str | Path) -> tuple[list[TradeCase], dict[str, Any]]:
    run_dir = Path(run_dir)
    trades = pl.read_parquet(run_dir / "trades.parquet")
    fills = pl.read_parquet(run_dir / "fills.parquet")
    orders = pl.read_parquet(run_dir / "orders.parquet") if (run_dir / "orders.parquet").exists() else pl.DataFrame()
    sig_paths = sorted((run_dir / "signals").glob("**/*.parquet"))
    signals = pl.read_parquet(sig_paths) if sig_paths else pl.DataFrame()
    cases: list[TradeCase] = []
    for t in trades.iter_rows(named=True):
        f = fills.filter(pl.col("signal_id") == t["signal_id"])
        entry_fee = float(f.filter(pl.col("kind") == "ENTRY")["fee"].sum())
        tp1_qty = float(f.filter(pl.col("kind") == "TP1")["qty"].sum())
        support, resistance, atr = _signal_info(signals, t["signal_id"])
        if atr is None:
            feats = t.get("features")
            if isinstance(feats, str):
                try:
                    feats = json.loads(feats)
                except json.JSONDecodeError:
                    feats = {}
            atr = feats.get("atr") if isinstance(feats, dict) else None
        qty = float(t["quantity"])
        initial_stop = float(t["stop_price"])
        if orders.height:
            stops = orders.filter(
                (pl.col("symbol") == t["symbol"])
                & (pl.col("kind") == "STOP")
                & (pl.col("ts_ms") >= int(t["entry_ts_ms"]))
                & (pl.col("ts_ms") <= int(t["exit_ts_ms"]))
            ).sort("ts_ms")
            if stops.height:
                initial_stop = float(stops["price"][0])
        if t["side"] == "LONG" and initial_stop >= float(t["entry_price"]):
            initial_stop = float(t["stop_price"])
        if t["side"] == "SHORT" and initial_stop <= float(t["entry_price"]):
            initial_stop = float(t["stop_price"])
        cases.append(
            TradeCase(
                trade_id=t["trade_id"],
                signal_id=t["signal_id"],
                symbol=t["symbol"],
                side=t["side"],
                entry_ts_ms=int(t["entry_ts_ms"]),
                exit_ts_ms=int(t["exit_ts_ms"]),
                entry_price=float(t["entry_price"]),
                stop_price=initial_stop,
                final_stop_price=float(t["stop_price"]),
                tp1_price=float(t["tp1_price"]),
                tp2_price=float(t["tp2_price"]) if t["tp2_price"] is not None else None,
                quantity=qty,
                tp1_qty=min(tp1_qty, qty),
                rest_qty=max(qty - min(tp1_qty, qty), 0.0),
                risk_usdt=float(t["risk_usdt"]),
                actual_net_r=float(t["net_r"]),
                actual_exit_reason=str(t["exit_reason"]),
                entry_fee=entry_fee,
                support=float(support) if support is not None else None,
                resistance=float(resistance) if resistance is not None else None,
                atr=float(atr) if atr is not None else None,
            )
        )
    meta = json.loads((run_dir / "metadata.json").read_text()) if (run_dir / "metadata.json").exists() else {}
    return cases, meta


def _stop_fill(bar: dict, stop: float, is_long: bool) -> tuple[float, str, float]:
    """Return (fill price, reason, adverse price movement in price units)."""
    slip = STOP_SLIP_BPS / 10_000.0
    if is_long:
        if float(bar["open"]) <= stop:
            return float(bar["open"]), "STOP_GAP", stop - float(bar["open"])
        return stop * (1.0 - slip), "STOP", stop * slip
    if float(bar["open"]) >= stop:
        return float(bar["open"]), "STOP_GAP", float(bar["open"]) - stop
    return stop * (1.0 + slip), "STOP", stop * slip


def _market_fill(price: float, is_long: bool) -> tuple[float, float]:
    slip = MARKET_SLIP_BPS / 10_000.0
    if is_long:
        return price * (1.0 - slip), price * slip
    return price * (1.0 + slip), price * slip


def _pnl(side_sign: float, entry: float, exit_price: float, qty: float) -> float:
    return (exit_price - entry) * side_sign * qty


def _funding_pnl(case: TradeCase, exit_ts_ms: int, funding: pl.DataFrame | None) -> float:
    if funding is None or funding.height == 0:
        return 0.0
    rows = funding.filter((pl.col("symbol") == case.symbol) & (pl.col("ts_ms") > case.entry_ts_ms) & (pl.col("ts_ms") <= exit_ts_ms))
    if rows.height == 0:
        return 0.0
    notional = case.quantity * case.entry_price
    return float(-case.sign * (rows["rate"] * notional).sum())


def _finalize(
    case: TradeCase,
    arm: str,
    legs: list[Leg],
    exit_ts_ms: int,
    bars_held: int,
    ambiguous: bool,
    funding: pl.DataFrame,
    exit_reason: str,
) -> ExitResult:
    gross = sum(_pnl(case.sign, case.entry_price, leg.price, leg.qty) for leg in legs)
    exit_fees = sum(leg.price * leg.qty * (MAKER if leg.liquidity == "MAKER" else TAKER) for leg in legs)
    funding_pnl = _funding_pnl(case, exit_ts_ms, funding)
    net = gross - case.entry_fee - exit_fees + funding_pnl
    slip = sum(
        (abs(leg.price - case.entry_price) * leg.qty)
        for leg in legs
        if leg.reason.startswith("STOP") or leg.reason in ("BREAKOUT", "TIME_STOP", "HORIZON")
    )
    return ExitResult(
        arm=arm,
        net_pnl=net,
        net_r=net / case.risk_usdt if case.risk_usdt > 0 else 0.0,
        exit_reason=exit_reason or (legs[-1].reason if legs else ""),
        exit_ts_ms=exit_ts_ms,
        bars_held=bars_held,
        ambiguous=ambiguous,
        funding_pnl=funding_pnl,
        fees=case.entry_fee + exit_fees,
        slippage_pnl=slip,
        legs=legs,
    )


def _two_leg(
    case: TradeCase,
    bars: list[dict],
    funding: pl.DataFrame,
    *,
    stop: float,
    tp1: float,
    tp2: float | None,
    breakout_level: float | None,
    time_stop_ms: int | None,
    arm: str,
) -> ExitResult:
    q1 = case.tp1_qty
    q2 = case.rest_qty
    if q1 <= 0 or q2 <= 0:
        q1, q2 = case.quantity, 0.0
    legs: list[Leg] = []
    ambiguous = False
    for i, bar in enumerate(bars):
        ts = int(bar["ts_close_ms"])
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        stop_hit = low <= stop if case.is_long else high >= stop
        tp1_hit = high >= tp1 if case.is_long else low <= tp1
        tp2_hit = (tp2 is not None) and (high >= tp2 if case.is_long else low <= tp2)
        breakout_hit = False
        if breakout_level is not None:
            breakout_hit = close <= breakout_level if case.is_long else close >= breakout_level
        if stop_hit and (tp1_hit or tp2_hit or breakout_hit):
            ambiguous = True
        if stop_hit:
            px, reason, _ = _stop_fill(bar, stop, case.is_long)
            legs.append(Leg(px, q1 + q2, reason, "TAKER"))
            return _finalize(case, arm, legs, ts, i + 1, ambiguous, funding, reason)
        if breakout_hit:
            px, _ = _market_fill(close, case.is_long)
            legs.append(Leg(px, q1 + q2, "BREAKOUT", "TAKER"))
            return _finalize(case, arm, legs, ts, i + 1, ambiguous, funding, "BREAKOUT")
        if q1 > 0 and tp1_hit:
            legs.append(Leg(tp1, q1, "TP1", "MAKER"))
            q1 = 0.0
        if q2 > 0 and tp2_hit and tp2 is not None:
            legs.append(Leg(tp2, q2, "TP2", "MAKER"))
            q2 = 0.0
        if q1 <= 0 and q2 <= 0:
            return _finalize(case, arm, legs, ts, i + 1, ambiguous, funding, "TP2")
        if time_stop_ms is not None and ts - case.entry_ts_ms >= time_stop_ms:
            px, _ = _market_fill(close, case.is_long)
            legs.append(Leg(px, q1 + q2, "TIME_STOP", "TAKER"))
            return _finalize(case, arm, legs, ts, i + 1, ambiguous, funding, "TIME_STOP")
    px, _ = _market_fill(float(bars[-1]["close"]), case.is_long)
    legs.append(Leg(px, q1 + q2, "HORIZON", "TAKER"))
    return _finalize(case, arm, legs, int(bars[-1]["ts_close_ms"]), len(bars), ambiguous, funding, "HORIZON")


def _single(
    case: TradeCase,
    bars: list[dict],
    funding: pl.DataFrame,
    *,
    stop: float,
    target: float | None,
    trail_mult: float | None,
    be_at: float | None,
    structural_n: int | None,
    pre_bars: list[dict],
    time_stop_ms: int | None,
    arm: str,
) -> ExitResult:
    qty = case.quantity
    current_stop = stop
    highest = case.entry_price
    lowest = case.entry_price
    hist_high = [float(b["high"]) for b in pre_bars]
    hist_low = [float(b["low"]) for b in pre_bars]
    ambiguous = False
    for i, bar in enumerate(bars):
        ts = int(bar["ts_close_ms"])
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        # Check the working stop from the previous close, before any update.
        stop_hit = low <= current_stop if case.is_long else high >= current_stop
        target_hit = target is not None and (high >= target if case.is_long else low <= target)
        if stop_hit and target_hit:
            ambiguous = True
        if stop_hit:
            px, reason, _ = _stop_fill(bar, current_stop, case.is_long)
            return _finalize(case, arm, [Leg(px, qty, reason, "TAKER")], ts, i + 1, ambiguous, funding, reason)
        if target_hit and target is not None:
            return _finalize(case, arm, [Leg(target, qty, "TARGET", "MAKER")], ts, i + 1, ambiguous, funding, "TARGET")
        highest = max(highest, high)
        lowest = min(lowest, low)
        mfe_r = ((highest - case.entry_price) if case.is_long else (case.entry_price - lowest)) / case.risk_distance
        # Update BE/trailing only after the bar closes.
        if be_at is not None and mfe_r >= be_at:
            be = case.entry_price + case.sign * 0.07 * case.risk_distance
            current_stop = max(current_stop, be) if case.is_long else min(current_stop, be)
        if trail_mult is not None and case.atr is not None:
            trail = highest - trail_mult * case.atr if case.is_long else lowest + trail_mult * case.atr
            current_stop = max(current_stop, trail) if case.is_long else min(current_stop, trail)
        if structural_n is not None:
            # A protective stop must stay on the protective side of the current
            # close. A 24h/48h extreme can be above a long entry (the entry is a
            # new low); using it would create a fake profit stop.
            if case.is_long:
                structural = min(hist_low[-structural_n:])
                if structural < close:
                    current_stop = max(current_stop, structural)
            else:
                structural = max(hist_high[-structural_n:])
                if structural > close:
                    current_stop = min(current_stop, structural)
        hist_high.append(high)
        hist_low.append(low)
        if time_stop_ms is not None and ts - case.entry_ts_ms >= time_stop_ms:
            px, _ = _market_fill(close, case.is_long)
            return _finalize(case, arm, [Leg(px, qty, "TIME_STOP", "TAKER")], ts, i + 1, ambiguous, funding, "TIME_STOP")
    px, _ = _market_fill(float(bars[-1]["close"]), case.is_long)
    return _finalize(case, arm, [Leg(px, qty, "HORIZON", "TAKER")], int(bars[-1]["ts_close_ms"]), len(bars), ambiguous, funding, "HORIZON")


def simulate_arm(case: TradeCase, bars: list[dict], pre_bars: list[dict], funding: pl.DataFrame, arm: str) -> ExitResult:
    if arm == "baseline_actual":
        return ExitResult(arm=arm, net_pnl=case.actual_net_r * case.risk_usdt, net_r=case.actual_net_r,
                          exit_reason=case.actual_exit_reason, exit_ts_ms=case.exit_ts_ms,
                          bars_held=max(0, (case.exit_ts_ms - case.entry_ts_ms) // 60_000), status="ACTUAL")
    if not bars:
        return ExitResult(arm=arm, status="NOT_RUN", note="no post-entry 1m bars")
    stop = case.stop_price
    if arm == "no_breakout":
        return _two_leg(case, bars, funding, stop=stop, tp1=case.tp1_price, tp2=case.tp2_price,
                        breakout_level=None, time_stop_ms=MS_8H, arm=arm)
    if arm == "stop_only":
        return _single(case, bars, funding, stop=stop, target=None, trail_mult=None, be_at=None,
                       structural_n=None, pre_bars=pre_bars, time_stop_ms=MS_8H, arm=arm)
    if arm in ("static_1r", "static_2r", "static_3r"):
        k = {"static_1r": 1.0, "static_2r": 2.0, "static_3r": 3.0}[arm]
        target = case.entry_price + case.sign * k * case.risk_distance
        return _single(case, bars, funding, stop=stop, target=target, trail_mult=None, be_at=None,
                       structural_n=None, pre_bars=pre_bars, time_stop_ms=None, arm=arm)
    if arm == "no_tp":
        return _single(case, bars, funding, stop=stop, target=None, trail_mult=None, be_at=None,
                       structural_n=None, pre_bars=pre_bars, time_stop_ms=MS_8H, arm=arm)
    if arm in ("chandelier_2.5", "chandelier_3.0"):
        mult = 2.5 if arm.endswith("2.5") else 3.0
        if case.atr is None or case.atr <= 0:
            return ExitResult(arm=arm, status="NOT_RUN", note="missing ATR")
        return _single(case, bars, funding, stop=stop, target=None, trail_mult=mult, be_at=None,
                       structural_n=None, pre_bars=pre_bars, time_stop_ms=None, arm=arm)
    if arm in ("be_0.5", "be_1.0"):
        if case.atr is None or case.atr <= 0:
            return ExitResult(arm=arm, status="NOT_RUN", note="missing ATR")
        be = 0.5 if arm.endswith("0.5") else 1.0
        return _single(case, bars, funding, stop=stop, target=None, trail_mult=2.5, be_at=be,
                       structural_n=None, pre_bars=pre_bars, time_stop_ms=None, arm=arm)
    if arm in ("structural_24h", "structural_48h"):
        n = 1440 if arm.endswith("24h") else 2880
        if len(pre_bars) < n:
            return ExitResult(arm=arm, status="NOT_RUN", note=f"insufficient pre-entry history for {n}-bar structure")
        return _single(case, bars, funding, stop=stop, target=None, trail_mult=None, be_at=None,
                       structural_n=n, pre_bars=pre_bars, time_stop_ms=None, arm=arm)
    if arm in ("breakout_0.0", "breakout_0.6"):
        if case.support is None or case.resistance is None or case.atr is None:
            return ExitResult(arm=arm, status="NOT_RUN", note="missing support/resistance/ATR")
        threshold = 0.0 if arm.endswith("0.0") else 0.6
        level = (
            case.support - threshold * case.atr
            if case.is_long
            else case.resistance + threshold * case.atr
        )
        return _two_leg(case, bars, funding, stop=stop, tp1=case.tp1_price, tp2=case.tp2_price,
                        breakout_level=level, time_stop_ms=MS_8H, arm=arm)
    return ExitResult(arm=arm, status="NOT_RUN", note="arm not implemented")


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "sum_r": 0.0, "mean_r": None, "median_r": None, "pf": None, "win_rate": None, "max_dd_r": 0.0}
    arr = values
    wins = sum(x for x in arr if x > 0)
    losses = -sum(x for x in arr if x < 0)
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for x in arr:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    s = sorted(arr)
    n = len(arr)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {
        "n": n, "sum_r": sum(arr), "mean_r": sum(arr) / n, "median_r": med,
        "pf": (wins / losses) if losses > 0 else None,
        "win_rate": sum(1 for x in arr if x > 0) / n,
        "max_dd_r": dd,
    }


def scan(run_dir: str | Path, paths: Paths | None = None, horizon_ms: int = MS_7D) -> dict[str, Any]:
    paths = paths or Paths()
    cases, meta = load_cases(run_dir)
    funding_all = pl.read_parquet(paths.funding / "funding.parquet") if (paths.funding / "funding.parquet").exists() else pl.DataFrame()
    results: dict[str, list[ExitResult]] = {a: [] for a in ARMS}
    not_run: list[dict[str, Any]] = []
    for case in cases:
        bars_df = load_bars_frame(paths, case.symbol, case.entry_ts_ms - MS_48H, case.entry_ts_ms + horizon_ms)
        if bars_df.height == 0:
            not_run.append({"trade_id": case.trade_id, "arm": "all", "reason": "no 1m data"})
            continue
        rows = bars_df.to_dicts()
        pre = [r for r in rows if int(r["ts_close_ms"]) <= case.entry_ts_ms]
        post = [r for r in rows if int(r["ts_open_ms"]) >= case.entry_ts_ms]
        funding = funding_all.filter(pl.col("symbol") == case.symbol) if funding_all.height else pl.DataFrame()
        for arm in ARMS:
            res = simulate_arm(case, post, pre, funding, arm)
            res.trade_id = case.trade_id
            if res.status == "NOT_RUN":
                not_run.append({"trade_id": case.trade_id, "arm": arm, "reason": res.note})
            else:
                results[arm].append(res)
    out: dict[str, Any] = {
        "source_run_dir": str(run_dir),
        "source_run_id": meta.get("run_id"),
        "source_config_hash": meta.get("config_hash"),
        "diagnostic_only": True,
        "portfolio_equity_claimed": False,
        "entry_set": f"baseline_actual_{len(cases)}_trades",
        "horizon_hours": horizon_ms / 3_600_000,
        "arms": {},
        "not_run": not_run,
        "rules": {
            "same_bar": "stop-first, ambiguous count reported",
            "trailing": "updated only after the bar closes",
            "stop_slippage_bps": STOP_SLIP_BPS,
            "market_slippage_bps": MARKET_SLIP_BPS,
            "fees": {"maker": MAKER, "taker": TAKER},
            "funding": "rate * entry notional; long pays positive rate",
        },
    }
    for arm, rows in results.items():
        vals = [r.net_r for r in rows]
        reasons: dict[str, int] = {}
        for r in rows:
            reasons[r.exit_reason] = reasons.get(r.exit_reason, 0) + 1
        out["arms"][arm] = {
            **_summary(vals),
            "ambiguous": sum(1 for r in rows if r.ambiguous),
            "exit_reasons": reasons,
            "avg_bars_held": (sum(r.bars_held for r in rows) / len(rows)) if rows else None,
            "fees": sum(r.fees for r in rows),
            "funding": sum(r.funding_pnl for r in rows),
            "delta_vs_baseline_mean_r": None,
            "per_trade": [
                {"trade_id": r.trade_id, "net_r": r.net_r, "exit_reason": r.exit_reason,
                 "bars_held": r.bars_held, "ambiguous": r.ambiguous, "status": r.status}
                for i, r in enumerate(rows)
            ],
        }
    base_mean = out["arms"]["baseline_actual"].get("mean_r")
    for arm in ARMS:
        m = out["arms"][arm].get("mean_r")
        if base_mean is not None and m is not None:
            out["arms"][arm]["delta_vs_baseline_mean_r"] = m - base_mean
    out["not_run_counts"] = {}
    for x in not_run:
        out["not_run_counts"][x["arm"]] = out["not_run_counts"].get(x["arm"], 0) + 1
    return out


def render_markdown(scan_result: dict[str, Any]) -> str:
    md = [
        "# Exit-policy diagnostic scan",
        "",
        f"Source: `{scan_result['source_run_dir']}` (run `{scan_result.get('source_run_id')}`).",
        f"**Diagnostic only.** Same {scan_result['entry_set']} entries and quantities; future entries, equity, overlap and kill-switch timing are not re-simulated.",
        "",
        "| arm | n | sum R | mean R | PF | win | max DD R | delta mean R | ambiguous |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, a in scan_result["arms"].items():
        md.append(
            f"| {arm} | {a['n']} | {a['sum_r']:+.3f} | {a['mean_r']:+.4f} | {a['pf'] if a['pf'] is not None else '-'} | "
            f"{a['win_rate']:.3f} | {a['max_dd_r']:.3f} | {a['delta_vs_baseline_mean_r']:+.4f} | {a['ambiguous']} |"
        )
    md += ["", "## Exit reason mix", ""]
    for arm, a in scan_result["arms"].items():
        md.append(f"* `{arm}`: {a['exit_reasons']}")
    md += ["", "## NOT_RUN", ""]
    if scan_result.get("not_run_counts"):
        for arm, n in sorted(scan_result["not_run_counts"].items()):
            md.append(f"* `{arm}`: {n}")
    else:
        md.append("None.")
    md += [
        "",
        "The scan is conditional on the baseline trade set. A better arm here is a candidate for a full Nautilus replay, not evidence of profit.",
        "",
    ]
    return "\n".join(md)

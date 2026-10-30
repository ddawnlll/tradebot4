"""Run the static capital-viability diagnostic over baseline trade envelopes.

This script is read-only with respect to trading behaviour. It loads the
development baseline run artifacts referenced by a validation report (never the
sealed holdout), evaluates every trade envelope independently at the 20 USDT and
500 USDT tiers, and writes ``reports/research/v8_transfer/capital_viability.json``.

Missing instrument filters or trade fields are recorded as UNKNOWN and the run
continues. The output is a diagnostic, not a tradability or profitability claim.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bot.data.instruments import load_instruments
from bot.data.storage import Paths
from bot.risk.capital import (
    SCENARIO_TIERS,
    Candidate,
    CapitalFilters,
    Envelope,
    evaluate_tiers,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "reports" / "research" / "v8_transfer" / "capital_viability.json"
SOURCE_KEYS = ("champion_dev", "oos")  # never "vault" / holdout


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def latest_validation(root: Path) -> Path:
    candidates = sorted((root / "reports").glob("validation_*/validation.json"))
    if not candidates:
        raise FileNotFoundError("no reports/validation_*/validation.json found")
    return candidates[-1]


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def run_settings(run_dir: Path) -> dict[str, Any]:
    cfg = load_yaml(run_dir / "config.yaml")
    risk_fraction = _as_float(cfg.get("risk_fraction"))
    leverage = _as_float(cfg.get("leverage"))
    slippage_bps = _as_float(cfg.get("market_slippage_bps"))
    return {
        "risk_fraction": 0.05 if risk_fraction is None else risk_fraction,
        "leverage": 10.0 if leverage is None else leverage,
        "slippage_bps_per_side": 2.0 if slippage_bps is None else slippage_bps,
        "two_leg_tp": bool(cfg.get("two_leg_tp", False)),
        "config_found": bool(cfg),
    }


def load_trade_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "trades.parquet"
    if not path.exists():
        return []
    import polars as pl

    return pl.read_parquet(path).to_dicts()


def effective_stop(row: dict[str, Any], side: str, entry: float | None) -> tuple[float | None, str]:
    """Return (stop_price, source) for the capital envelope.

    The baseline ``stop_price`` column is the trade's final/breakeven stop for
    trailed exits, so it can sit on the wrong side of entry. In that case the
    initial risk distance is recovered from the recorded ``risk_usdt`` and
    ``quantity`` (gross loss per unit) and the source is labelled explicitly.
    Nothing is invented when both are unavailable.
    """
    stop = _as_float(row.get("stop_price"))
    if entry is None:
        return stop, "stop_price"
    direction_ok = stop is not None and (
        (side == "LONG" and stop < entry) or (side == "SHORT" and stop > entry)
    )
    if direction_ok:
        return stop, "stop_price"

    risk_usdt = _as_float(row.get("risk_usdt"))
    quantity = _as_float(row.get("quantity"))
    if risk_usdt is not None and quantity is not None and risk_usdt > 0 and quantity > 0:
        distance = risk_usdt / quantity
        if distance > 0 and math.isfinite(distance):
            derived = entry - distance if side == "LONG" else entry + distance
            return derived, "risk_usdt_per_quantity"
    return stop, "stop_price"


def add_envelope(bucket: dict[str, list[Envelope]], env: Envelope) -> None:
    bucket[env.symbol].append(env)


def capital_metrics(envelopes: list[Envelope], equity: float) -> dict[str, Any]:
    """Tier-specific headroom from the explicit required-equity term."""
    required = [env.e_required_usdt for env in envelopes if env.e_required_usdt is not None]
    if not required:
        return {
            "candidates_with_known_required_equity": 0,
            "min_required_equity_usdt": None,
            "max_required_equity_usdt": None,
            "equity_headroom_usdt": None,
            "candidates_requiring_more_than_tier": 0,
        }
    max_required = max(required)
    return {
        "candidates_with_known_required_equity": len(required),
        "min_required_equity_usdt": min(required),
        "max_required_equity_usdt": max_required,
        "equity_headroom_usdt": equity - max_required,
        "candidates_requiring_more_than_tier": sum(1 for value in required if value > equity),
    }


def build_report(validation_path: Path, out_path: Path) -> dict[str, Any]:
    validation = json.loads(validation_path.read_text())
    data_gaps: list[str] = []
    sources: list[dict[str, Any]] = []
    specs: dict[str, Any] = {}
    try:
        specs = load_instruments(Paths())
    except Exception as exc:
        data_gaps.append(f"instrument specs unavailable ({type(exc).__name__}: {exc}); all filters UNKNOWN")

    v8_candidates = [
        ROOT / "v8-core" / "src" / "usdm_sim" / "capital_viability.rs",
        ROOT.parent / "v8" / "v8-core" / "src" / "usdm_sim" / "capital_viability.rs",
    ]
    v8_ref = next((path for path in v8_candidates if path.exists()), None)
    if v8_ref is None:
        data_gaps.append(
            "v8-core/src/usdm_sim/capital_viability.rs was not found in this checkout; "
            "the E_step/E_notional/E_margin/E_crit formulas were ported from the task specification"
        )

    tier_envs: dict[str, list[Envelope]] = {tier.label: [] for tier in SCENARIO_TIERS}
    tier_symbols: dict[str, dict[str, list[Envelope]]] = {
        tier.label: defaultdict(list) for tier in SCENARIO_TIERS
    }
    tier_sources: dict[str, dict[str, list[Envelope]]] = {
        tier.label: defaultdict(list) for tier in SCENARIO_TIERS
    }
    tier_labels = {tier.label: tier.equity for tier in SCENARIO_TIERS}
    missing_specs: set[str] = set()
    unknown_filter_symbols: dict[str, list[str]] = defaultdict(list)
    stop_sources: dict[str, int] = defaultdict(int)

    for source_key in SOURCE_KEYS:
        block = validation.get(source_key)
        if not isinstance(block, dict) or not block.get("run_dir"):
            data_gaps.append(f"validation source {source_key!r} missing run_dir")
            continue
        run_dir = Path(block["run_dir"])
        settings = run_settings(run_dir)
        rows = load_trade_rows(run_dir)
        source_info = {
            "key": source_key,
            "run_dir": str(run_dir),
            "trades": len(rows),
            "risk_fraction": settings["risk_fraction"],
            "leverage": settings["leverage"],
            "slippage_bps_per_side": settings["slippage_bps_per_side"],
            "two_leg_tp": settings["two_leg_tp"],
            "config_found": settings["config_found"],
        }
        sources.append(source_info)
        if not rows:
            data_gaps.append(f"no trades.parquet rows for {source_key} at {run_dir}")
            continue
        if not settings["config_found"]:
            data_gaps.append(f"config.yaml missing for {source_key} at {run_dir}; used documented defaults")

        for row in rows:
            symbol = str(row.get("symbol") or "UNKNOWN")
            side = str(row.get("side") or "UNKNOWN").upper()
            entry_price = _as_float(row.get("entry_price"))
            stop_price, stop_source = effective_stop(row, side, entry_price)
            stop_sources[stop_source] += 1
            spec = specs.get(symbol)
            if spec is None:
                missing_specs.add(symbol)
                filters = CapitalFilters()
            else:
                filters = CapitalFilters.from_instrument_spec(spec)
            if filters.unknown_critical_fields and symbol not in unknown_filter_symbols:
                unknown_filter_symbols[symbol] = list(filters.unknown_critical_fields)

            candidate = Candidate(
                symbol=symbol,
                side=side,
                entry_price=entry_price,  # type: ignore[arg-type]
                stop_price=stop_price,  # type: ignore[arg-type]
                quantity=_as_float(row.get("quantity")),  # type: ignore[arg-type]
                equity=20.0,  # replaced per tier, never scaled
                leverage=settings["leverage"],
                risk_fraction=settings["risk_fraction"],
                filters=filters,
                legs=2 if settings["two_leg_tp"] else 1,
                entry_is_maker=False,
                slippage_bps_per_side=settings["slippage_bps_per_side"],
                stop_distance_source=stop_source,
            )
            for env in evaluate_tiers(candidate):
                tier_envs[env.tier_label].append(env)
                add_envelope(tier_symbols[env.tier_label], env)
                tier_sources[env.tier_label][source_key].append(env)

    if missing_specs:
        data_gaps.append(
            f"instrument specs missing for {len(missing_specs)} symbols; filters treated as UNKNOWN: "
            + ", ".join(sorted(missing_specs))
        )
    if unknown_filter_symbols:
        data_gaps.append(
            "UNKNOWN historical filter fields by symbol: "
            + json.dumps({k: v for k, v in sorted(unknown_filter_symbols.items())}, sort_keys=True)
        )
    if stop_sources.get("risk_usdt_per_quantity"):
        data_gaps.append(
            f"{stop_sources['risk_usdt_per_quantity']} trade rows had a final/breakeven stop on the wrong side "
            "of entry; initial stop distance was derived from recorded risk_usdt/quantity and labelled "
            "stop_distance_source=risk_usdt_per_quantity"
        )

    report: dict[str, Any] = {
        "generated": datetime.now(UTC).isoformat(),
        "diagnostic": {
            "static_diagnostic": True,
            "not_a_guarantee": (
                "This is a static capital-viability diagnostic of historical order envelopes. "
                "It is not a guarantee of tradability, fill probability, or profitability, and it "
                "does not size positions or change strategy behaviour."
            ),
            "formula_source": (
                "v8-core usdm_sim::capital_viability (E_step, E_notional, E_margin, E_crit) "
                "with an explicit separate fee/slippage reserve term"
            ),
            "separate_scenarios": True,
            "tier_note": (
                "The 20 USDT and 500 USDT tiers are evaluated independently with their own equity. "
                "The 500 USDT tier is not a scaled copy of the 20 USDT tier."
            ),
            "holdout_touched": False,
            "sealed_holdout_note": "Only validation keys champion_dev and oos were read; the vault was not opened.",
        },
        "scenario_tiers": [
            {"label": tier.label, "equity_usdt": tier.equity, "independent": True} for tier in SCENARIO_TIERS
        ],
        "sources": {
            "validation": str(validation_path),
            "runs": sources,
            "instruments": str(Paths().instruments / "instruments.parquet"),
            "v8_reference": str(v8_ref) if v8_ref is not None else "UNKNOWN",
        },
        "stop_distance_sources": dict(sorted(stop_sources.items())),
        "summary": {label: summarize(envs) for label, envs in tier_envs.items()},
        "by_tier": {
            label: {
                "equity_usdt": tier_labels[label],
                "summary": summarize(envs),
                "capital": capital_metrics(envs, tier_labels[label]),
                "by_symbol": {
                    symbol: {
                        **summarize(sym_envs),
                        "capital": capital_metrics(sym_envs, tier_labels[label]),
                    }
                    for symbol, sym_envs in sorted(tier_symbols[label].items())
                },
                "by_source": {
                    source: summarize(src_envs) for source, src_envs in sorted(tier_sources[label].items())
                },
            }
            for label, envs in tier_envs.items()
        },
        "data_gaps": data_gaps,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, default=None, help="validation.json to read")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    validation_path = args.validation or latest_validation(ROOT)
    report = build_report(validation_path, args.out)

    print(f"wrote {args.out}")
    print("static diagnostic only; not a guarantee of tradability or profitability")
    for label, summary in report["summary"].items():
        print(
            f"{label}: candidates={summary['candidates']} approved={summary['approved']} "
            f"rejected={summary['rejected']} bindings={summary['binding_constraint_counts']}"
        )
    if report["data_gaps"]:
        print(f"data gaps: {len(report['data_gaps'])}")


if __name__ == "__main__":
    main()

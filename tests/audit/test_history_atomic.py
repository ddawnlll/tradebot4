"""History identity, idempotency and concurrent rewrite safety."""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import polars as pl

from bot.audit.why import append_history, audit_run, write_audit_report


def _run_dir(tmp_path):
    run_dir = tmp_path / "run_audit"
    run_dir.mkdir()
    trades = pl.DataFrame(
        [
            {"symbol": "AAA", "side": "LONG", "entry_ts_ms": 1_000, "exit_ts_ms": 2_000,
             "gross_pnl": 1.0, "fees": 0.05, "funding": 0.0, "net_pnl": 0.95, "net_r": 0.95,
             "mae_r": -0.2, "mfe_r": 1.0, "exit_reason": "TP1", "bars_held": 10},
            {"symbol": "BBB", "side": "SHORT", "entry_ts_ms": 5_000, "exit_ts_ms": 6_000,
             "gross_pnl": 0.5, "fees": 0.05, "funding": -0.01, "net_pnl": 0.46, "net_r": 0.46,
             "mae_r": -0.1, "mfe_r": 0.5, "exit_reason": "STOP_BREAKEVEN", "bars_held": 8},
        ]
    )
    trades.write_parquet(run_dir / "trades.parquet")
    (run_dir / "metrics.json").write_text(json.dumps({"profit_factor": 0.5, "expectancy_r": -0.1}))
    return run_dir


def _metrics() -> dict:
    return {"profit_factor": 0.5, "expectancy_r": -0.2, "win_rate": 0.5,
            "max_drawdown_pct": 55.0, "final_equity": 9.0, "strategy": "test"}


def test_duplicate_append_is_a_noop(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric=_metrics())
    path = tmp_path / "history.parquet"
    append_history(audit, path, "a", "NO_GO")
    append_history(audit, path, "a", "NO_GO")
    assert pl.read_parquet(path).height == 1


def test_different_source_same_run_does_not_duplicate(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric=_metrics())
    audit["config_hash"] = "cfg-1"
    audit["validation_source"] = "validation_a"
    path = tmp_path / "history.parquet"
    first = append_history(audit, path, "source-a", "NO_GO")
    second = append_history(audit, path, "source-b", "NO_GO")
    assert first.height == second.height == 1
    df = pl.read_parquet(path)
    assert df.height == 1
    assert df["source"][0] == "source-a"


def test_same_run_different_period_is_a_new_row(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric=_metrics())
    path = tmp_path / "history.parquet"
    append_history(audit, path, "a", "NO_GO")
    changed = copy.deepcopy(audit)
    changed["risk"]["last_entry"] = "1970-01-01T00:00:09Z"
    append_history(changed, path, "a", "NO_GO")
    assert pl.read_parquet(path).height == 2


def test_history_records_machine_readable_identity(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric=_metrics())
    audit["run_id"] = "run_identity"
    audit["validation_source"] = "validation_20260101T000000Z"
    audit["config_hash"] = "deadbeef"
    audit["periods"] = {"dev": [1, 2], "oos": [2, 3], "vault": [3, 4]}
    path = tmp_path / "history.parquet"
    append_history(audit, path, "validation_20260101T000000Z", "NO_GO")
    row = pl.read_parquet(path).row(0, named=True)
    assert row["run_id"] == "run_identity"
    assert row["validation_source"] == "validation_20260101T000000Z"
    assert row["config_hash"] == "deadbeef"
    assert row["period_start"] and row["period_end"]
    assert row["identity_key"]
    assert json.loads(row["periods"])["dev"] == [1, 2]


def test_concurrent_appends_do_not_lose_rows(tmp_path):
    run = _run_dir(tmp_path)
    base = audit_run(run, metric=_metrics())
    path = tmp_path / "history.parquet"
    workers = 8
    barrier = Barrier(workers)

    def worker(index: int) -> None:
        audit = copy.deepcopy(base)
        audit["run_id"] = f"run_{index}"
        audit["config_hash"] = f"cfg_{index}"
        barrier.wait(timeout=10)
        append_history(audit, path, "concurrent", "NO_GO")
        append_history(audit, path, "concurrent", "NO_GO")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))

    df = pl.read_parquet(path)
    assert df.height == workers
    assert sorted(df["run_id"].to_list()) == [f"run_{i}" for i in range(workers)]
    assert df["identity_key"].n_unique() == workers


def test_append_upgrades_legacy_history_without_losing_rows(tmp_path):
    run = _run_dir(tmp_path)
    base = audit_run(run, metric=_metrics())
    path = tmp_path / "history.parquet"
    append_history(base, path, "old", "NO_GO")
    legacy = pl.read_parquet(path).drop(
        ["validation_source", "config_hash", "periods", "identity_key"]
    )
    legacy.write_parquet(path)

    new_run = copy.deepcopy(base)
    new_run["run_id"] = "run_new"
    append_history(new_run, path, "new", "NO_GO")

    df = pl.read_parquet(path)
    assert df.height == 2
    assert set(df["run_id"].to_list()) == {Path(base["run_dir"]).name, "run_new"}
    assert "identity_key" in df.columns


def _validation(run, periods: dict, config_hash: str = "abc") -> dict:
    return {
        "generated": "2026-01-01T00:00:00+00:00",
        "base_config": {"starting_balance": 20.0, "strategy": "test"},
        "periods": periods,
        "champion_dev": {
            "run_id": run.name,
            "run_dir": str(run),
            "metrics": _metrics(),
            "config_hash": config_hash,
        },
        "go_no_go": {"verdict": "NO_GO"},
    }


def _summary(identity: dict | None) -> dict:
    summary = {
        "random": {"expectancy_r": -0.15},
        "baseline": {"expectancy_r": -0.20},
        "hypotheses": {
            "H1": {
                "best": "h1a",
                "configs": {"h1a": {"trades": 10, "profit_factor": 0.9, "expectancy_r": -0.01}},
                "stress": {"profit_factor": 0.8},
            }
        },
    }
    if identity is not None:
        summary["identity"] = identity
    return summary


def test_stale_research_summary_is_not_attached(tmp_path):
    run = _run_dir(tmp_path)
    periods = {"dev": [1_000, 2_000], "oos": [2_000, 3_000], "vault": [3_000, 4_000]}
    root = tmp_path / "root"
    report_dir = root / "reports" / "research_round1"
    report_dir.mkdir(parents=True)
    summary_path = report_dir / "summary.json"
    summary_path.write_text(json.dumps(_summary(None)))
    (report_dir / "gates.json").write_text(json.dumps({"hypotheses": {}}))

    audit = write_audit_report(
        _validation(run, periods),
        root=root,
        doc_path=tmp_path / "doc.md",
        history_path=tmp_path / "history.parquet",
        research_summary=summary_path,
    )
    assert audit["hypotheses"] is None
    assert audit["random_expectancy"] is None
    assert audit["baseline_4f_expectancy"] is None
    assert audit["research_context"]["attached"] is False
    assert "identity" in audit["research_context"]["reason"]
    assert "Not attached" in (tmp_path / "doc.md").read_text()


def test_identity_mismatch_periods_is_not_attached(tmp_path):
    run = _run_dir(tmp_path)
    periods = {"dev": [1_000, 2_000], "oos": [2_000, 3_000], "vault": [3_000, 4_000]}
    root = tmp_path / "root"
    report_dir = root / "reports" / "research_round1"
    report_dir.mkdir(parents=True)
    summary_path = report_dir / "summary.json"
    summary_path.write_text(json.dumps(_summary({
        "run_id": run.name,
        "config_hash": "abc",
        "periods": {"dev": [1_000, 9_999]},
    })))
    audit = write_audit_report(
        _validation(run, periods),
        root=root,
        doc_path=tmp_path / "doc.md",
        history_path=tmp_path / "history.parquet",
        research_summary=summary_path,
    )
    assert audit["research_context"]["attached"] is False
    assert "periods" in audit["research_context"]["reason"]


def test_matching_research_identity_is_attached(tmp_path):
    run = _run_dir(tmp_path)
    periods = {"dev": [1_000, 2_000], "oos": [2_000, 3_000], "vault": [3_000, 4_000]}
    root = tmp_path / "root"
    report_dir = root / "reports" / "research_round1"
    report_dir.mkdir(parents=True)
    identity = {"run_id": run.name, "config_hash": "abc", "periods": periods}
    summary_path = report_dir / "summary.json"
    summary_path.write_text(json.dumps(_summary(identity)))
    (report_dir / "gates.json").write_text(json.dumps(
        {"hypotheses": {"H1": {"gates": {"h1a": {"configuration_verdict": False}}}}}
    ))

    audit = write_audit_report(
        _validation(run, periods),
        root=root,
        doc_path=tmp_path / "doc.md",
        history_path=tmp_path / "history.parquet",
        research_summary=summary_path,
    )
    assert audit["research_context"]["attached"] is True
    assert audit["random_expectancy"] == -0.15
    assert audit["baseline_4f_expectancy"] == -0.20
    assert audit["hypotheses"][0]["name"] == "H1"
    assert audit["history_rows"] == 1


def test_discovery_does_not_hardcode_research_round1(tmp_path):
    run = _run_dir(tmp_path)
    periods = {"dev": [1_000, 2_000], "oos": [2_000, 3_000], "vault": [3_000, 4_000]}
    root = tmp_path / "root"
    report_dir = root / "reports" / "research_round_xyz"
    report_dir.mkdir(parents=True)
    (report_dir / "summary.json").write_text(json.dumps(_summary({
        "run_id": run.name,
        "config_hash": "abc",
        "periods": periods,
    })))
    (report_dir / "gates.json").write_text(json.dumps({"hypotheses": {}}))

    audit = write_audit_report(
        _validation(run, periods),
        root=root,
        doc_path=tmp_path / "doc.md",
        history_path=tmp_path / "history.parquet",
    )
    assert audit["research_context"]["attached"] is True
    assert "research_round_xyz" in audit["research_context"]["summary"]

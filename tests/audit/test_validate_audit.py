"""The loss audit must never fail validation, but its failure must be visible."""

from __future__ import annotations

import json

from bot.backtest import validate


def _report() -> dict:
    return {
        "generated": "2026-01-01T00:00:00+00:00",
        "base_config": {"strategy": "test", "starting_balance": 20.0},
        "periods": {"dev": [1, 2], "oos": [2, 3], "vault": [3, 4]},
        "champion_dev": {"run_id": "run_x", "run_dir": "/tmp/run_x", "metrics": {}, "trades": 0},
        "walk_forward": [],
        "sensitivity": [],
        "ablations": [],
        "oos": {"metrics": {}, "trades": 0, "run_id": "run_oos"},
        "monte_carlo": {},
        "go_no_go": {"verdict": "NO_GO"},
    }


def test_audit_failure_is_visible_in_report(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("audit exploded")

    monkeypatch.setattr("bot.audit.why.write_audit_report", boom)
    report = _report()
    status = validate._attach_audit(report, tmp_path)
    assert status["ok"] is False
    assert "audit exploded" in status["error"]
    assert report["audit"] == status


def test_audit_failure_is_persisted_in_validation_json(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise ValueError("bad audit input")

    monkeypatch.setattr("bot.audit.why.write_audit_report", boom)
    report = _report()
    validate._finalize_report(report, tmp_path)
    written = json.loads((tmp_path / "validation.json").read_text())
    assert written["audit"]["ok"] is False
    assert "bad audit input" in written["audit"]["error"]


def test_audit_success_fields_are_recorded(monkeypatch, tmp_path):
    def ok(*args, **kwargs):
        return {
            "audit_doc": str(tmp_path / "doc.md"),
            "history_path": str(tmp_path / "history.parquet"),
            "history_rows": 3,
            "research_context": {"attached": False},
        }

    monkeypatch.setattr("bot.audit.why.write_audit_report", ok)
    report = _report()
    status = validate._attach_audit(report, tmp_path)
    assert status["ok"] is True
    assert status["doc"] == str(tmp_path / "doc.md")
    assert status["history_rows"] == 3
    assert status["run_id"] == "run_x"


def test_audit_disabled_is_marked_skipped(tmp_path):
    report = _report()
    status = validate._attach_audit(report, tmp_path, enabled=False)
    assert status["ok"] is False
    assert status["skipped"] is True

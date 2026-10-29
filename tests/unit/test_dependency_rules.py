"""Enforce the architectural rule: core is framework independent."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "bot"

FORBIDDEN_IN_CORE = ("nautilus_trader", "binance", "streamlit", "duckdb", "torch", "sklearn")
FORBIDDEN_IN_DATA = ("streamlit",)
NAUTILUS_ALLOWED = {"src/bot/backtest", "src/bot/data/to_nautilus.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_core_has_no_framework_imports() -> None:
    offenders = []
    for path in (SRC / "core").rglob("*.py"):
        bad = _imports(path) & set(FORBIDDEN_IN_CORE)
        if bad:
            offenders.append((_rel(path), sorted(bad)))
    assert not offenders, offenders


def test_data_layer_has_no_ui_imports() -> None:
    offenders = []
    for path in (SRC / "data").rglob("*.py"):
        bad = _imports(path) & set(FORBIDDEN_IN_DATA)
        if bad:
            offenders.append((_rel(path), sorted(bad)))
    assert not offenders, offenders


def test_nautilus_only_in_backtest_and_catalog_export() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = _rel(path)
        if rel.startswith("src/bot/backtest") or rel == "src/bot/data/to_nautilus.py":
            continue
        if "nautilus_trader" in _imports(path):
            offenders.append(rel)
    assert not offenders, offenders


def test_core_does_not_import_adapters() -> None:
    offenders = []
    for path in (SRC / "core").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bot."):
                if node.module.startswith(("bot.backtest", "bot.data", "bot.execution", "bot.journal", "bot.kaizen")):
                    offenders.append((_rel(path), node.module))
    assert not offenders, offenders

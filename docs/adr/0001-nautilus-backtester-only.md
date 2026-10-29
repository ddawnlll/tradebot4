# ADR 0001: NautilusTrader v2 is a backtester only

Status: accepted (2026-10-29)

## Context

NautilusTrader v2 is a release candidate with a Rust core and a PyO3 control
plane. Its live adapters, risk engine and execution semantics change between
release candidates. The project needs a trustworthy execution simulator, but
its trading logic must survive framework churn and must be identical in
backtest, shadow and live.

## Decision

`nautilus_trader==2.0.0rc8` is pinned and used only for:

* historical bar replay and simulated matching,
* fee, fill and funding accounting,
* the Parquet catalog used to feed `BacktestNode`,
* backtest reports.

All market intelligence, strategy logic, sizing, risk policy, journaling and
live execution live in framework-independent Python. The core emits
`OrderIntent`; the Nautilus bridge translates it.

## Consequences

* A Nautilus upgrade cannot silently change strategy behaviour.
* The same decision code can run in a live adapter without Nautilus.
* We must maintain a thin bridge and accept that some v2 features (funding
  events, catalog funding data) are missing and worked around.
* Golden tests compare `BacktestNode` and the low-level engine on identical
  data.

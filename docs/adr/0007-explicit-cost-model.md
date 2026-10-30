# ADR 0007: Explicit cost model for backtests

Status: accepted (2026-10-30)

## Context

The Milestone 6 baseline used Nautilus defaults: one-tick probabilistic
slippage and a 100% limit fill on touch. On a 20 USDT account, a 2 bps market
slippage and a 5 bps stop slippage are material, and a limit that only touches
its price is not guaranteed to fill. Costs were also spread across the runner,
the bridge and ad hoc assumptions, which made the baseline hard to audit.

## Decision

All execution costs live in `src/bot/backtest/fill_models.py` and are frozen in
`config/gates.yaml` for stress tests:

1. Commission: maker 0.020%, taker 0.050% (VIP0, no BNB discount).
2. Market entry and exit: `max(2 bps, 1 tick)` adverse. Implemented by
   returning a synthetic L1 book with one level at the adverse price, so the
   worse price flows through the account and the trade record.
3. Stop-market: 5 bps adverse on top of any bar gap, using the same synthetic
   book mechanism.
4. Limit orders: the market must trade at least one tick through the limit.
   A mere touch returns an empty book and no fill. An additional 90% touch
   fill probability models queue uncertainty.
5. Funding: a symbol with bars in the run range but missing or gappy funding
   aborts the run instead of silently assuming zero.
6. Stress: commission x1.5 and slippage x2.0 via config multipliers.

## Consequences

The account, position, trade and report all use the same worse fill price, so
the equity change still equals the sum of net PnL (tested). Backtests are
slower only by the Python fill-model callback. The old costed baseline is not
comparable to the new one without stating the model; the baseline report keeps
both columns and explains the difference.
